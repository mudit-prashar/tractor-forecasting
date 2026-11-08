"""Extend the supplied history with reproducible, recency-weighted mock rows."""

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(source, output, manifest, start, end, seed=20261108, half_life_days=365):
    if start > end or half_life_days <= 0 or not math.isfinite(half_life_days):
        raise ValueError('Dates must be ordered and half-life must be positive and finite')
    if len({source.resolve(), output.resolve(), manifest.resolve()}) != 3:
        raise ValueError('Source, output, and manifest must be different files')
    original = source.read_bytes()
    with source.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        rows = list(reader)
    observed = [date.fromisoformat(row['Date']) for row in rows]
    latest = max(observed)
    if start <= latest:
        raise ValueError('Mock extension must start after all supplied observations')
    weights = [2 ** (-(latest - day).days / half_life_days) for day in observed]
    # Keep the supplied observation frequency. Row grain is still assumed additive.
    daily_rate = len(rows) / ((latest - min(observed)).days + 1)
    generated = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        # A separate seed per day keeps earlier output stable when extending the end date.
        rng = random.Random(f'{seed}:{day.isoformat()}')
        product, count = 1.0, -1
        while product > math.exp(-daily_rate):
            product *= rng.random()
            count += 1
        for row in rng.choices(rows, weights=weights, k=count):
            generated.append({**row, 'Date': day.isoformat()})
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(generated)
    weight_totals = Counter()
    for day, weight in zip(observed, weights):
        weight_totals[str(day.year)] += weight
    total_weight = sum(weights)
    details = {
        'kind': 'synthetic extension; not observed business data',
        'generator': 'scripts/generate_mock_history.py',
        'source': source.name,
        'source_sha256': hashlib.sha256(original).hexdigest(),
        'output': output.name,
        'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'start_date': start.isoformat(), 'end_date': end.isoformat(),
        'seed': seed, 'half_life_days': half_life_days,
        'method': 'Whole-row bootstrap with replacement; weight = 2 ** (-age_days / half_life_days), age measured from latest supplied observation. Only the date is replaced.',
        'source_sampling_probability_by_year': {year: round(weight / total_weight, 6)
                                                 for year, weight in sorted(weight_totals.items())},
        'rows': len(generated),
        'rows_by_year': dict(sorted(Counter(row['Date'][:4] for row in generated).items())),
        'rows_by_factory': dict(sorted(Counter(row['Warehouse_Location'] for row in generated).items())),
        'assumptions': [
            'Observation frequency follows the supplied average daily rate with Poisson variation.',
            'The one-year recency half-life is a configurable demo assumption, not fitted or validated.',
            'No future trend, seasonality, inflation path, or supplier improvement is inferred.',
            'Generated rows reuse historical values and associations; they are not independent evidence or a valid real-world evaluation set.',
            'Historical inventory observations do not seed physical component stock.',
        ],
    }
    manifest.write_text(json.dumps(details, indent=2) + '\n')
    return details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/supply_chain.csv')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/supply_chain_mock_2024_2026.csv')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/mock_history_manifest.json')
    parser.add_argument('--start', type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument('--end', type=date.fromisoformat, default=date(2026, 11, 7))
    parser.add_argument('--seed', type=int, default=20261108)
    parser.add_argument('--half-life-days', type=float, default=365)
    args = parser.parse_args()
    result = generate(args.source, args.output, args.manifest, args.start, args.end,
                      args.seed, args.half_life_days)
    print(json.dumps({key: result[key] for key in ('rows', 'rows_by_year', 'rows_by_factory')}))


if __name__ == '__main__':
    main()
