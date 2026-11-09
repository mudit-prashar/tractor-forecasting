"""Inspectable statistical baselines and component replenishment calculations."""

import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean


def percentile(values, fraction):
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = math.floor(index)
    return values[lower] + (values[math.ceil(index)] - values[lower]) * (index - lower)


def simulate(needs, opening, receipts):
    stock = opening
    shortage = 0
    stock_days = 0
    first_shortage = None
    for day, need in enumerate(needs):
        stock += receipts.get(day, 0)
        missed = max(0, need - stock)
        if missed > 0.0001 and first_shortage is None:
            first_shortage = day
        shortage += missed
        stock = max(0, stock - need)
        stock_days += stock
    return {'shortage_units': math.ceil(shortage - 1e-8), 'ending_stock': round(stock),
            'stock_days': stock_days, 'first_shortage_day': first_shortage}


def build_report(config, observations, fixture, inventory, incoming):
    start = date.fromisoformat(fixture['planning_date'])
    selected_model = config['model']
    if selected_model and selected_model not in fixture['bom']:
        raise ValueError('Unknown tractor model')
    models = [selected_model] if selected_model else list(fixture['bom'])
    bom = {model: fixture['bom'][model] for model in models}
    components = [item for item in fixture['components']
                  if any(parts.get(item['id'], 0) > 0 for parts in bom.values())]
    relevant_suppliers = {supplier for item in components for supplier in item['suppliers']}
    observations = [row for row in observations if row['observed_date'] < start
                    and row['tractor_model'] in models]
    if not observations:
        raise ValueError('No observations for the report model before the planning date')
    horizon = config['horizon_days']
    end = start + timedelta(days=horizon - 1)
    weekly = defaultdict(lambda: defaultdict(int))
    supplier_rows = defaultdict(list)
    for row in observations:
        observed = row['observed_date']
        if observed >= start:
            continue
        week = observed - timedelta(days=observed.weekday())
        weekly[row['tractor_model']][week] += row['demand_units']
        if row['supplier'] in relevant_suppliers:
            supplier_rows[row['supplier']].append(row)
    latest = max(row['observed_date'] for row in observations)
    earliest = min(row['observed_date'] for row in observations)
    orders = [{**order, 'due_date': (start + timedelta(days=order['due_in_days'])).isoformat()}
              for order in fixture['orders'] if order['due_in_days'] < horizon and order['model'] in models]
    daily_by_model = {}
    model_forecasts = []
    history = defaultdict(float)
    future = defaultdict(lambda: {'units': 0, 'lower': 0, 'upper': 0})
    adjustment = 1 + config['demand_change_pct'] / 100
    for model in models:
        # Include complete source calendar weeks only. Unobserved weeks are omitted.
        points = sorted((week, units) for week, units in weekly[model].items()
                        if week >= earliest and week + timedelta(days=6) <= latest)
        if len(points) < 16:
            raise ValueError(f'At least 16 observed complete weeks are required for {model}')
        recent = [units for _, units in points[-8:]]
        daily = mean(recent) / 7 * adjustment
        low = percentile(recent, 0.1) / 7 * adjustment
        high = percentile(recent, 0.9) / 7 * adjustment
        needs = [daily] * horizon
        for order in orders:
            if order['model'] == model:
                # Confirmed orders are part of demand, not added on top of it twice.
                day = order['due_in_days']
                confirmed = sum(o['quantity'] for o in orders if o['model'] == model and o['due_in_days'] == day)
                needs[day] = max(needs[day], confirmed)
        daily_by_model[model] = needs
        train = [units for _, units in points[:-8]]
        actual = [units for _, units in points[-8:]]
        backtest_mean = mean(train[-8:])
        mae = mean(abs(value - backtest_mean) for value in actual)
        wape = sum(abs(value - backtest_mean) for value in actual) / max(sum(actual), 1) * 100
        model_forecasts.append({'model': model, 'predicted_units': round(sum(needs)),
                                'daily_units': round(daily, 1), 'holdout_mae': round(mae, 1),
                                'holdout_wape_pct': round(wape, 1), 'observed_weeks': len(points)})
        for week, units in points[-12:]:
            history[week.isoformat()] += units
        for day, need in enumerate(needs):
            when = start + timedelta(days=day)
            week = (when - timedelta(days=when.weekday())).isoformat()
            future[week]['units'] += need
            # This is empirical historical spread, not a calibrated probability interval.
            future[week]['lower'] += min(low, need)
            future[week]['upper'] += max(high, need)
    suppliers = []
    metrics = {}
    for supplier, rows in sorted(supplier_rows.items()):
        delays = [row['supplier_delay_days'] for row in rows]
        rates = [row['component_failure_rate'] for row in rows]
        last_seen = max(row['observed_date'] for row in rows)
        metrics[supplier] = {'delay': mean(delays), 'failure': mean(rates)}
        suppliers.append({'supplier': supplier, 'observations': len(rows),
                          'expected_delay_days': round(mean(delays) + config['extra_delay_days'], 1),
                          'historical_p90_delay_days': round(percentile(delays, .9), 1),
                          'failure_rate_pct': round(mean(rates) * 100, 2),
                          'last_observed': last_seen.isoformat(),
                          'stale': (start - last_seen).days > 30})
    items, recommendations, risks = [], [], []
    for component in components:
        cid = component['id']
        needs = [sum(daily_by_model[model][day] * parts.get(cid, 0)
                     for model, parts in bom.items()) for day in range(horizon)]
        balance = inventory[cid]
        available = balance['on_hand'] - balance['reserved']
        receipts = defaultdict(float)
        in_transit = 0
        for order in incoming:
            if order['component_id'] != cid:
                continue
            if selected_model and order.get('model') and order['model'] != selected_model:
                continue
            offset = (order['expected_date'] - start).days
            if 0 <= offset < horizon:
                in_transit += order['quantity']
                receipts[offset] += order['quantity'] * (1 - order['failure_rate'])
        baseline = simulate(needs, available, receipts)
        daily_need = sum(needs) / horizon
        eligible = [supplier for supplier in component['suppliers'] if supplier in metrics]
        missing = [supplier for supplier in component['suppliers'] if supplier not in metrics]
        item = {'id': cid, 'name': component['name'], **balance, 'available': available,
                'incoming_units': in_transit, 'required_units': math.ceil(sum(needs)),
                'coverage_days': round(available / max(daily_need, 1), 1),
                'projected_shortage': baseline['shortage_units'],
                'projected_ending_stock': baseline['ending_stock'],
                'unit_cost': component['unit_cost'], 'eligible_suppliers': eligible,
                'suppliers_without_history': missing,
                'failure_rate_proxy_pct': round(mean(metrics[supplier]['failure'] for supplier in eligible) * 100, 2) if eligible else None}
        items.append(item)
        if missing:
            risks.append({'id': f'{cid}-supplier-history', 'kind': 'Supply', 'severity': 'Medium',
                          'title': f"{component['name']} supplier history missing",
                          'detail': f"No observations in this report's model scope for {', '.join(missing)}. Those suppliers are excluded from purchase comparisons."})
        if baseline['first_shortage_day'] is not None:
            risks.append({'id': cid, 'kind': 'Inventory', 'severity': 'High',
                          'title': f"{component['name']} shortage",
                          'detail': f"First projected shortage on {(start + timedelta(days=baseline['first_shortage_day'])).isoformat()}; {baseline['shortage_units']:,} component units unmet without a new order."})
        candidates = []
        penalty = component['unit_cost'] * 3
        candidates.append({'supplier': None, 'shipping': 'No order', 'quantity': 0,
                           'arrival_date': None, 'purchase_cost': 0,
                           'modeled_cost': round(baseline['shortage_units'] * penalty + baseline['stock_days'] * component['unit_cost'] * .25 / 365, 2),
                           'shortage_units': baseline['shortage_units'], 'ending_stock': baseline['ending_stock']})
        for supplier in eligible:
            observed = metrics[supplier]
            settings = fixture['suppliers'][supplier]
            failure = observed['failure']
            needed = max(0, sum(needs) - available - sum(receipts.values()))
            quantity = math.ceil(needed / (1 - failure))
            if not quantity:
                continue
            for shipping in ('Standard', 'Expedited'):
                # Expedited timing is a mock contractual assumption, not a trained prediction.
                lead = (math.ceil(settings['base_lead_days'] + observed['delay']) if shipping == 'Standard' else 3) + config['extra_delay_days']
                price = component['unit_cost'] * settings['price_factor'] * (1.4 if shipping == 'Expedited' else 1)
                planned = dict(receipts)
                planned[lead] = planned.get(lead, 0) + quantity * (1 - failure)
                outcome = simulate(needs, available, planned)
                cost = quantity * price + outcome['stock_days'] * component['unit_cost'] * .25 / 365 + outcome['shortage_units'] * penalty
                candidates.append({'supplier': supplier, 'shipping': shipping, 'quantity': quantity,
                                   'arrival_date': (start + timedelta(days=lead)).isoformat(),
                                   'failure_rate': failure, 'purchase_cost': round(quantity * price, 2),
                                   'modeled_cost': round(cost, 2), 'shortage_units': outcome['shortage_units'],
                                   'ending_stock': outcome['ending_stock']})
        ranked = sorted(candidates, key=lambda candidate: candidate['modeled_cost'])
        choice = ranked[0]
        if choice['quantity']:
            recommendations.append({'id': cid, 'component': component['name'], **choice,
                                    'status': 'pending', 'avoided_shortage_units': baseline['shortage_units'] - choice['shortage_units'],
                                    'reason': f"{item['coverage_days']} days of available coverage against {horizon} days of requirements. This option has the lowest modeled cost among {len(candidates)} tested choices.",
                                    'candidates': ranked})
    for supplier in suppliers:
        risks.append({'id': supplier['supplier'], 'kind': 'Supply', 'severity': 'Medium',
                      'title': f"{supplier['supplier']} delivery exposure",
                      'detail': f"Expected extra delay {supplier['expected_delay_days']} days; historical p90 {supplier['historical_p90_delay_days']} days. " + ('Supplier observations are over 30 days old.' if supplier['stale'] else 'Based on recorded supplier history.')})
    return {
        'planning_date': start.isoformat(), 'end_date': end.isoformat(), 'site': fixture['site'],
        'scope': {'model': selected_model or None, 'models': models,
                  'stock_policy': 'shared_unreserved_factory_stock'},
        'history_provenance': fixture.get('history_provenance', 'Supplied synthetic historical observations.'),
        'config': config, 'summary': {'forecast_units': sum(item['predicted_units'] for item in model_forecasts),
                                    'inventory_value': sum(item['on_hand'] * item['unit_cost'] for item in items),
                                    'components_at_risk': sum(item['projected_shortage'] > 0 for item in items),
                                    'recommended_purchase_cost': round(sum(item['purchase_cost'] for item in recommendations), 2)},
        'forecast': {'method': 'Trailing 8-week mean baseline', 'models': model_forecasts,
                     'history': [{'week': week, 'units': round(units)} for week, units in sorted(history.items())],
                     'future': [{'week': week, **{key: round(value) for key, value in values.items()}} for week, values in sorted(future.items())],
                     'range_label': '10th–90th percentile of the last 8 observed weeks; uncalibrated',
                     'evaluation': 'Last 8 complete observed weeks held out; forecast trained only on earlier weeks.'},
        'suppliers': suppliers, 'inventory': items, 'recommendations': recommendations, 'risks': risks,
        'customer_orders': orders,
        'assumptions': [fixture['provenance'], fixture.get('history_provenance', 'Supplied synthetic historical observations.'), 'CSV warehouse location codes are treated as factory identifiers for this demo. Each factory uses only its own source observations and separate operational stock and planned orders.',
                        'Demand observations are assumed additive; missing calendar weeks are unobserved, not zero.',
                        'The report model selection applies to demand, customer orders, supplier history, relevant BOM components, and purchase recommendations. All models combines requirements only within this factory.',
                        'The tractor-to-component bill of materials and supplier eligibility are mock fixture assumptions, not mappings inferred from the CSV.',
                        'Physical component stock is shared at the factory. This scenario uses its relevant unreserved stock and does not reserve it for a model. Separate model reports are independent scenarios, not additive stock allocations.',
                        'Model-specific approved supply is included only for that model or an all-model report. Factory-wide and legacy approved orders remain shared incoming supply.',
                        'Confirmed customer orders replace forecast demand on their due dates when larger. Mock reservations represent separate pre-horizon production and are already deducted from available stock.',
                        'Delay and failure estimates are historical mean baselines. Aggregate failure rates are reused as component defect proxies; they do not predict individual failures.',
                        'Expedited delivery assumes 3 days plus scenario delay and a 40% price premium.',
                        'Candidate ranking uses one purchase per component, expected defective receipts, 25% annual holding cost and a per-unit shortage penalty of 3× component cost. It is not a production scheduling optimizer.',
                        'Approvals create internal planned orders, not external purchases. Actual stock changes only when goods are received.'],
        'fixture_version': fixture['version'], 'source_rows': len(observations),
    }
