"""按有效日期解释省表批次，并对周内来源范围作核对。

产地目录可能在同一个统计周内连续改码（例如 HB-01→HB-02、随后
HB-02→HB-03）。解析时必须从原始批次代码出发，按生效日顺序逐条套用，
因此后一条规则可以作用到前一条规则产生的代码上；生效日之前的记录保持
原归属，不因后来的并入而回填改写。
"""
from datetime import date, timedelta

from .ledger import entities

PENDING = '待核'
SETTLED = '已定'


def _revision_entities(rows):
    return sorted(
        (row for row in rows if row['type'] == 'origin_revision'),
        key=lambda row: (row['payload']['effective_on'], row['payload']['region'],
                         row['payload']['period'], row['payload']['old_origin'],
                         row['id'], row['version']),
    )


def _resolve(source_batch, region, period, day, revisions):
    """从原始批次代码沿生效日顺序追到当日最终代码。

    返回 (最终代码, 已套用的规则实体列表)。逐轮扫描直到没有新规则可套用：
    后到的规则若把代码改为某个已有规则的旧代码，仍会继续并入，最终得到
    当日的最终有效产地。仅在生效日当天及之后套用规则，因此 HB-02 的历史
    两天不会因为之后并入 HB-03 而变成 HB-03。已套用的规则不重复套用，
    可防止目录出现环形改码时无限循环。
    """
    origin = source_batch
    applied = []
    seen = set()
    changed = True
    while changed:
        changed = False
        for revision in revisions:
            key = (revision['id'], revision['version'])
            if key in seen:
                continue
            payload = revision['payload']
            if (payload['region'] != region or payload['period'] != period or
                    payload['old_origin'] != origin):
                continue
            if day < date.fromisoformat(payload['effective_on']):
                continue
            origin = payload['new_origin']
            applied.append(revision)
            seen.add(key)
            changed = True
    return origin, applied


def map_rows(rows):
    """把分省表原始行解析为当日最终产地，并保留套用过的规则。

    该函数只做纯映射，不判断资料是否齐备，供采集覆盖与视图共用。
    """
    revisions = _revision_entities(rows)
    output = []
    for source in rows:
        if source['type'] != 'province_table':
            continue
        payload = source['payload']
        for item in payload['rows']:
            day = date.fromisoformat(item['day'])
            origin, applied = _resolve(
                item['batch'], payload['region'], payload['period'], day, revisions)
            output.append({
                'region': payload['region'],
                'period': payload['period'],
                'day': item['day'],
                'source_batch': item['batch'],
                'origin': origin,
                'weight_kg': item['weight_kg'],
                'source_sha256': source['source'],
                'mapping_revisions': len(applied),
                'mapping_path': [item['batch']] + [
                    change['payload']['new_origin'] for change in applied],
                'applied_rules': [(change['id'], change['version'])
                                  for change in applied],
            })
    return sorted(output, key=lambda item: (item['region'], item['period'], item['day']))


def pending_rule_sources(connection, collection):
    """来源目录中尚未取得或尚未达到要求版次的必需产地改码资料。

    只有产地改码会令周内归属悬而未决；公告或分省表缺失属于覆盖问题，
    不在这里判定。没有来源目录（例如直接重放收据）时视为无待取规则。
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_sources'"
    ).fetchone()
    if not exists:
        return []
    rows = connection.execute(
        'SELECT s.source_url, s.expected_revision, '
        '  (SELECT GROUP_CONCAT(DISTINCT e.version) FROM entities e '
        '   JOIN receipts r ON r.collection=e.collection AND r.body_sha256=e.receipt_sha256 '
        "   WHERE e.collection=s.collection AND e.entity_type='origin_revision' "
        '   AND r.source_url=s.source_url) '
        'FROM collection_sources s '
        'LEFT JOIN collection_progress p '
        'ON p.collection=s.collection AND p.source_url=s.source_url '
        "WHERE s.collection=? AND s.source_kind='origin_revision' AND s.required=1 "
        'ORDER BY s.source_url',
        (collection,)).fetchall()
    pending = []
    for url, expected_revision, versions in rows:
        received = set(filter(None, (versions or '').split(',')))
        if not received or (expected_revision and expected_revision not in received):
            pending.append(url)
    return pending


def _apply_pending(mapped, missing_sources):
    """标记受待取改码影响的当日归属。

    尚未取得的改码规则没有生效日可用，不能假定它落在周内哪一天之后，
    因此该统计周每天的最终归属都保持待核。规则齐备后按各自生效日逐日
    解析；生效日之前的历史归属保持原代码，不因后来并入而回填。
    """
    for item in mapped:
        pending = bool(missing_sources)
        item['attribution_status'] = PENDING if pending else SETTLED
        item['pending_rule_sources'] = list(missing_sources) if pending else []
    return mapped


def records(connection, collection):
    """原始批次 -> 当日最终产地的追码结果。

    每行都保留 `source_batch`（原文代码）与 `mapping_path`（逐条规则后的
    代码链）。`origin` 是当前可解析到的最终代码；规则尚未齐备时
    `attribution_status` 为待核，说明该代码仍是暂定值。
    """
    missing = pending_rule_sources(connection, collection)
    return _apply_pending(map_rows(entities(connection, collection)), missing)


def weekly(connection, collection):
    """按当日最终产地拆分周内记录。

    缺失改码规则时生效日未知，整周归属都可能被后续规则改写，因此每个
    分组都标为待核；分组本身仍按当前可解析代码展示，供核对使用，但不
    得当作可比较的最终拆分。
    """
    grouped = {}
    for item in records(connection, collection):
        key = (item['region'], item['period'], item['origin'])
        grouped.setdefault(key, []).append(item)
    result = []
    for (region, period, origin), items in sorted(grouped.items()):
        days = [item['day'] for item in items]
        statuses = {item['attribution_status'] for item in items}
        result.append({
            'region': region,
            'period': period,
            'origin': origin,
            'attribution_status': PENDING if PENDING in statuses else SETTLED,
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
    weekly_rows = weekly(connection, collection)
    reports = []
    for bulletin in bulletins:
        payload = bulletin['payload']
        observed = [item for item in mapped if item['region'] == payload['region'] and
                    item['period'] == payload['period']]
        year, week = map(int, payload['period'].split('-W'))
        start = date.fromisocalendar(year, week, 1)
        expected_days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
        received_days = sorted({item['day'] for item in observed})
        # 省级均重直接使用原始逐日记录，绝不把产地分组均值再次相加。
        mean = (sum(item['weight_kg'] for item in observed) / len(observed)
                if observed else None)
        missing_days = [day for day in expected_days if day not in received_days]
        pending_days = sorted({item['day'] for item in observed
                               if item['attribution_status'] == PENDING})
        pending_sources = sorted({source for item in observed
                                  for source in item['pending_rule_sources']})
        if missing_days:
            status = '覆盖不完整'
        elif pending_days:
            status = PENDING
        else:
            status = '可比较'
        reports.append({
            'region': payload['region'],
            'period': payload['period'],
            'basis': payload['basis'],
            'bulletin_weight_kg': payload['weight_kg'],
            'province_mean_kg': mean,
            'difference_kg': round(mean - payload['weight_kg'], 4) if mean is not None else None,
            'received_days': received_days,
            'missing_days': missing_days,
            'pending_days': pending_days,
            'attribution_status': PENDING if pending_days else SETTLED,
            'pending_rule_sources': pending_sources,
            'origin_split_ready': not missing_days and not pending_days,
            'origins': [item for item in weekly_rows
                        if item['region'] == payload['region'] and item['period'] == payload['period']],
            'status': status,
        })
    return reports
