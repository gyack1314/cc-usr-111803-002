# 来源字段

weight_bulletin 与 province_table 的 region 是来源声明的关联线索；字段相等不证明两份原文已经完整或同版。

来源目录中的 expected_revision 表示该链接当前要求核实的来源版次；normal.json 为 v1，revised.json 将第一份来源改指同链接 v2。旧版原文仍可用于历史视图。
# 来源契约

`province_table` 的 `rows` 是按日记录的列表，每项包含 `day`、原始 `batch` 和 `weight_kg`。`origin_revision` 的 `effective_on` 当日起改变批次代码解释，原始行仍保留。`weight_bulletin` 与分省表按省份、统计周和交易均重口径比较；周度记录缺日时只能展示缺口，不得补零后发布为可比较结果。
