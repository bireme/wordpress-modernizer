from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from .enums import WidgetEventType


@dataclass(frozen=True)
class WidgetOption:
    table: str
    name: str
    value: bytes
    autoload: str


@dataclass(frozen=True)
class WidgetSnapshot:
    options: Tuple[WidgetOption, ...]

    @classmethod
    def from_options(cls, options: Iterable[WidgetOption]) -> "WidgetSnapshot":
        return cls(tuple(sorted(options, key=lambda item: (item.table, item.name))))


@dataclass(frozen=True)
class WidgetEvent:
    event_type: WidgetEventType
    table: str
    option_name: str


def compare_widgets(before: WidgetSnapshot, after: WidgetSnapshot) -> Tuple[WidgetEvent, ...]:
    left: Dict[Tuple[str, str], WidgetOption] = {
        (item.table, item.name): item for item in before.options
    }
    right: Dict[Tuple[str, str], WidgetOption] = {
        (item.table, item.name): item for item in after.options
    }
    events = []
    for key in sorted(left.keys() - right.keys()):
        events.append(WidgetEvent(WidgetEventType.WIDGET_OPTION_REMOVED, *key))
    for key in sorted(right.keys() - left.keys()):
        events.append(WidgetEvent(WidgetEventType.WIDGET_OPTION_ADDED, *key))
    for key in sorted(left.keys() & right.keys()):
        if left[key].value != right[key].value or left[key].autoload != right[key].autoload:
            kind = (
                WidgetEventType.SIDEBAR_MAPPING_CHANGED
                if key[1] == "sidebars_widgets"
                else WidgetEventType.WIDGET_OPTION_CHANGED
            )
            events.append(WidgetEvent(kind, *key))
    return tuple(events)
