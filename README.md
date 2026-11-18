# 生猪交易重量公告与产地批次归属

项目保存交易重量公告和按日记录的分省表原文。采集目录记录各来源版次，查询会按统计期间把公告与分省数据对应起来，并依据产地代码的生效日解释每条记录；同一周内可连续改码（HB-01→HB-02→HB-03），追码从原始批次沿生效日逐条套用，得到当日最终产地。缺少日期不能按零参与周均重比较；改码规则尚未取齐时整周归属保持待核，不能发布当前比较。已发布视图保存当时采用的口径，后续代码调整只影响当前结果。合成样例不访问外部站点，来源字段见 docs/source-contract.md。

在项目根目录运行：

    python -m unittest discover -s tests -v
    python -m compileall -q capture_service tests
    python -m capture_service.cli inspect data/normal.json

容器运行：docker build -t pork-collection-002 .；docker run --rm pork-collection-002。
