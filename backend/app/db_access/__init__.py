"""数据库侧 Text-to-SQL 目录、受控 SELECT 执行、结果展示与行→outputs 映射（中性命名）。"""

# --- 当前编排/测试实际使用的包级导出 ---
from app.db_access.catalog import catalog_prompt_text
from app.db_access.result_present import build_citation_snippets, build_query_result_template
from app.db_access.row_outputs import build_search_task_outputs
from app.db_access.scoped_executor import (
    execute_user_scoped_sql,
    is_valid_select_with_from,
)

# --- 以下为扩展点或子模块自用符号，暂不挂在包根（保留导入语句便于按需恢复）---
# from app.db_access.catalog import (
#     DEFAULT_LABEL_OVERRIDES,
#     DEFAULT_TABLE_CATALOG,
#     DictTableCatalog,
#     TableCatalog,
#     TableSpec,
#     build_column_display_index,
#     get_default_catalog,
#     label_for_column,
# )
# from app.db_access.policies import SqlRowOutputPolicy, policy_from_app_settings
# from app.db_access.result_present import (
#     ORDER_STATUS_DISPLAY,
#     format_cell,
#     format_line_items_brief,
#     format_row_for_display,
# )
# from app.db_access.row_outputs import unpaid_order_ids_from_rows
# from app.db_access.scoped_executor import (
#     PREFLIGHT_SQL_CHECKS,
#     extract_sql_primary_table,
#     register_sql_preflight_check,
#     validate_sql_before_execute,
# )

__all__ = [
    "build_citation_snippets",  #构建引用片段
    "build_query_result_template", #构建查询结果模板
    "build_search_task_outputs", #构建搜索任务输出
    "catalog_prompt_text", #构建表目录提示词
    "execute_user_scoped_sql", #执行用户受控SQL
    "is_valid_select_with_from", #验证SQL是否有效
]

# --- 下列名称曾为包级导出，现仅作清单保留（与上方注释掉的 import 对应）---
# __all__ 扩展备用：
# "DEFAULT_LABEL_OVERRIDES",
# "DEFAULT_TABLE_CATALOG",
# "DictTableCatalog",
# "ORDER_STATUS_DISPLAY",
# "PREFLIGHT_SQL_CHECKS",
# "SqlRowOutputPolicy",
# "TableCatalog",
# "TableSpec",
# "build_column_display_index",
# "extract_sql_primary_table",
# "format_cell",
# "format_line_items_brief",
# "format_row_for_display",
# "get_default_catalog",
# "label_for_column",
# "policy_from_app_settings",
# "register_sql_preflight_check",
# "unpaid_order_ids_from_rows",
# "validate_sql_before_execute",
