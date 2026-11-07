"""Validate source observations without silently deduplicating dimensional keys."""

import csv
import hashlib
import math
from datetime import date
from pathlib import Path

COLUMNS = [
    'Date', 'Tractor_Model', 'Demand_Units', 'Supplier', 'Supplier_Delay_Days',
    'Component_Failure_Rate', 'Inventory_Levels', 'Warehouse_Location',
    'Inflation_Rate', 'Market_Trend_Index',
]


def read_dataset(path: Path) -> tuple[str, list[tuple]]:
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    observations = []
    with path.open(newline='', encoding='utf-8-sig') as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f'Unexpected CSV columns: {reader.fieldnames}')
        for row_number, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None or not value.strip() for value in row.values()):
                    raise ValueError('Missing or extra CSV cells')
                demand = int(row['Demand_Units'])
                delay = int(row['Supplier_Delay_Days'])
                failure = float(row['Component_Failure_Rate'])
                inventory = int(row['Inventory_Levels'])
                inflation = float(row['Inflation_Rate'])
                trend = float(row['Market_Trend_Index'])
                if not all(math.isfinite(v) for v in (failure, inflation, trend)):
                    raise ValueError('Non-finite numeric value')
                if min(demand, delay, inventory) < 0 or not 0 <= failure <= 1:
                    raise ValueError('Invalid units, delay, inventory, or failure rate')
                observations.append((
                    row_number, date.fromisoformat(row['Date']), row['Tractor_Model'],
                    demand, row['Supplier'], delay, failure, inventory,
                    row['Warehouse_Location'], inflation, trend,
                ))
            except (ValueError, TypeError) as error:
                raise ValueError(f'Invalid CSV row {row_number}: {error}') from error
    if not observations:
        raise ValueError('The dataset is empty')
    return checksum, observations

