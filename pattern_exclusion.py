try:
    from .subscriber_off_scoring import (
        DEFAULT_DB_PATH,
        get_pattern_exclusion_list,
        update_exclusion_table,
    )
except ImportError:
    from subscriber_off_scoring import (
        DEFAULT_DB_PATH,
        get_pattern_exclusion_list,
        update_exclusion_table,
    )
