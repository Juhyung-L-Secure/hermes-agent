ACTION_FIELDS = {
    'click': frozenset(['ref']),
    'navigate': frozenset(['url']),
    'type': frozenset(['ref', 'text']),
    'keypress': frozenset(['key']),
    'reload': frozenset(),
    'back': frozenset([]),
    'scroll': frozenset(['direction']),
    'snapshot': frozenset([]),
    'screenshot': frozenset([]),
    'get_images': frozenset([]),
    'list_tabs': frozenset([]),
    'switch_tab': frozenset(['target_id']),
    'close': frozenset([]),
}
ACTION_MODES = frozenset({"blocked", "allowed"})
