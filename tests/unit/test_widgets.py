import pytest

from wp_modernizer.domain.enums import WidgetEventType
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot, compare_widgets


def snapshot(*items):
    return WidgetSnapshot.from_options(items)


def option(name="widget_text", value=b"serialized\x00data", table="wp_options"):
    return WidgetOption(table, name, value, "yes")


def test_equal_snapshot_has_no_events_and_preserves_binary() -> None:
    item = option()
    assert compare_widgets(snapshot(item), snapshot(item)) == ()
    assert snapshot(item).options[0].value == b"serialized\x00data"


@pytest.mark.parametrize(
    "before,after,event",
    [
        (snapshot(), snapshot(option()), WidgetEventType.WIDGET_OPTION_ADDED),
        (snapshot(option()), snapshot(), WidgetEventType.WIDGET_OPTION_REMOVED),
        (
            snapshot(option(value=b"a")),
            snapshot(option(value=b"b")),
            WidgetEventType.WIDGET_OPTION_CHANGED,
        ),
        (
            snapshot(option("sidebars_widgets", b"a")),
            snapshot(option("sidebars_widgets", b"b")),
            WidgetEventType.SIDEBAR_MAPPING_CHANGED,
        ),
    ],
)
def test_widget_events(before, after, event) -> None:
    assert compare_widgets(before, after)[0].event_type is event


def test_multisite_tables_remain_independent() -> None:
    before = snapshot(option(table="wp_options"), option(table="wp_2_options"))
    after = snapshot(option(table="wp_options"))
    event = compare_widgets(before, after)[0]
    assert event.table == "wp_2_options"
