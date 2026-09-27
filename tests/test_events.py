import json
import tempfile
import unittest
from pathlib import Path

from controller.events import EventTimeline


class EventTimelineTests(unittest.TestCase):
    def test_append_persists_and_reload_restores_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            timeline = EventTimeline(path, max_events=10)
            created = timeline.append(
                occurred_at=100.0,
                category="recommendation",
                severity="warning",
                title="Predicted congestion",
                details={"edge": [1, 2]},
            )

            restored = EventTimeline(path, max_events=10)
            events = restored.recent()

            self.assertEqual(events, (created,))
            with path.open(encoding="utf-8") as source:
                persisted = json.loads(source.readline())
            self.assertEqual(
                persisted["title"], "Predicted congestion"
            )

    def test_recent_is_bounded_and_filterable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            timeline = EventTimeline(
                Path(directory) / "events.jsonl",
                max_events=2,
            )
            timeline.append(
                occurred_at=1,
                category="controller",
                severity="info",
                title="one",
            )
            timeline.append(
                occurred_at=2,
                category="migration",
                severity="info",
                title="two",
            )
            timeline.append(
                occurred_at=3,
                category="migration",
                severity="error",
                title="three",
            )

            self.assertEqual(
                tuple(item.title for item in timeline.recent()),
                ("two", "three"),
            )
            self.assertEqual(
                tuple(
                    item.title
                    for item in timeline.recent(
                        category="migration"
                    )
                ),
                ("two", "three"),
            )
            self.assertEqual(
                tuple(
                    item.title
                    for item in timeline.recent(limit=1)
                ),
                ("three",),
            )

    def test_invalid_historical_line_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                "not-json\n"
                '{"event_id":"ok","occurred_at":1,'
                '"category":"test","severity":"info",'
                '"title":"valid","details":{}}\n'
            )

            timeline = EventTimeline(path)

            self.assertEqual(
                tuple(item.title for item in timeline.recent()),
                ("valid",),
            )


if __name__ == "__main__":
    unittest.main()