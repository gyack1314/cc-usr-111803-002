"""按有效日期解释省表批次，并对周内来源范围作核对。"""
from datetime import date, timedelta

from .ledger import entities


def records(connection, collection):
    rows = entities(connection, collection)
    revisions = sorted(
        (row['payload'] for row in rows if row['type'] == 'origin_revision'),
        key=lambda item: (item['effective_on'], item['region'], item['old_origin']),
    )
    output = []
    for source in rows:
        if source['type'] != 'province_table':
            continue
        payload = source['payload']
        for item in payload['rows']:
            origin = item['batch']
            applied = []
            for change in revisions:
                if (change['region'] == payload['region'] and
                        change['period'] == payload['period'] and
                        item['batch'] == change['old_origin'] and
                        date.fromisoformat(item['day']) >= date.fromisoformat(change['effective_on'])):
                    origin = change['new_origin']
                    applied.append(change)
            output.append({
                'region': payload['region'],
                'period': payload['period'],
                'day': item['day'],
                'source_batch': item['batch'],
                'origin': origin,
                'weight_kg': item['weight_kg'],
                'source_sha256': source['source'],
                'mapping_revisions': len(applied),
            })
    return sorted(output, key=lambda item: (item['region'], item['period'], item['day']))


def weekly(connection, collection):
    grouped = {}
    for item in records(connection, collection):
        key = (item['region'], item['period'], item['origin'])
        grouped.setdefault(key, []).append(item)
    result = []
    for (region, period, origin), items in sorted(grouped.items()):
        days = [item['day'] for item in items]
        result.append({
            'region': region,
            'period': period,
            'origin': origin,
            'days': days,
            'weight_kg': sum(item['weight_kg'] for item in items) / len(items),
            'source_batches': sorted({item['source_batch'] for item in items}),
            'source_sha256': sorted({item['source_sha256'] for item in items}),
        })
    return result


def comparison(connection, collection):
    bulletins = [row for row in entities(connection, collection)
                 if row['type'] == 'weight_bulletin' and row['payload']['basis'] == '交易均重']
    mapped = records(connection, collection)
    reports = []
    for bulletin in bulletins:
        payload = bulletin['payload']
        observed = [item for item in mapped if item['region'] == payload['region'] and
                    item['period'] == payload['period']]
        year, week = map(int, payload['period'].split('-W'))
        start = date.fromisocalendar(year, week, 1)
        expected_days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
        received_days = sorted({item['day'] for item in observed})
        mean = (sum(item['weight_kg'] for item in observed) / len(observed)
                if observed else None)
        reports.append({
            'region': payload['region'],
            'period': payload['period'],
            'basis': payload['basis'],
            'bulletin_weight_kg': payload['weight_kg'],
            'province_mean_kg': mean,
            'difference_kg': round(mean - payload['weight_kg'], 4) if mean is not None else None,
            'received_days': received_days,
            'missing_days': [day for day in expected_days if day not in received_days],
            'origins': [item for item in weekly(connection, collection)
                        if item['region'] == payload['region'] and item['period'] == payload['period']],
            'status': '可比较' if set(expected_days) <= set(received_days) else '覆盖不完整',
        })
    return reports
