"""Regression cases from the cross-publisher source and deduplication audit."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import collector
import sources
from model_hubs import recent_models
from release_text import headline_model


def source(sid):
    return next(s for s in sources.registry() if s["id"] == sid)


def page(sid, html):
    return list(sources.page_items(source(sid), html))


def feed(title, *, sid="openai", day="2026-11-16", body="", url="https://example.org/launch"):
    root = ET.Element("rss")
    entry = ET.SubElement(root, "item")
    for key, value in [("title", title), ("pubDate", day), ("description", body), ("link", url)]:
        ET.SubElement(entry, key).text = value
    return list(sources.feed_items(source(sid), ET.tostring(root)))


class IdentityIntegrityTests(unittest.TestCase):
    def test_unknown_suffixes_never_collapse_into_known_family(self):
        for org, names in [
            ("OpenAI", ["GPT-6.1 Sol", "GPT-6.1 Terra", "GPT-6.1 Unknown"]),
            ("Google", ["Gemini 4 Carbon", "Gemini 4 Hydrogen", "Gemini 4 Unknown"]),
            ("DeepSeek", ["DeepSeek-V3.2", "DeepSeek-V3.2-Speciale", "DeepSeek-V3.2-Future"]),
        ]:
            items = [sources.signal({"id": "news", "org": org}, name, name,
                                    "https://example.org/launch", "发布", "2026-11-16",
                                    model=headline_model("Introducing " + name, org)) for name in names]
            self.assertEqual({r["model"] for r in items}, set(names))
            self.assertEqual(len({collector.event_id(r) for r in items}), len(names))
            # Even unknown headline syntax must retain separate evidence.
            self.assertEqual(len({collector.event_id(dict(r, model="")) for r in items}), len(names))

    def test_same_url_and_date_do_not_hide_another_model(self):
        record = dict(org="DeepSeek", name="DeepSeek-V3.2", kind="发布",
                      date="2025-12-01", url="https://example.org/models")
        item = sources.signal({"id": "deepseek", "org": "DeepSeek"}, "speciale",
                              "DeepSeek-V3.2-Speciale", record["url"], "发布", record["date"],
                              model="DeepSeek-V3.2-Speciale")
        self.assertFalse(collector.already_catalogued(item, [record]))

    def test_unicode_hyphens_and_unversioned_gpt_names(self):
        for title, model in [("Introducing GPT‑6 Cyber", "GPT-6 Cyber"),
                             ("Introducing GPT–Live-1", "GPT-Live-1"),
                             ("Introducing GPT4o", "GPT4o"),
                             ("Introducing o5", "o5")]:
            item, = feed(title)
            self.assertEqual(item["model"], model)

    def test_shared_url_edits_and_long_notes_retain_distinct_evidence(self):
        preview, = feed("Introducing GPT-7 Preview", day="2026-11-15")
        final, = feed("Introducing GPT-7", day="2026-11-16")
        self.assertNotEqual(preview["id"], final["id"])
        self.assertNotEqual(collector.event_id(preview), collector.event_id(final))
        prefix = "Developer release details: " + "context " * 30
        notes = "".join(f"<p>{prefix}Gemini 5 {name} is now available.</p>" for name in ["Carbon", "Hydrogen"])
        items = feed("Release notes", sid="vertex", body=notes)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({r["id"] for r in items}), 2)
        self.assertEqual(len({collector.event_id(r) for r in items}), 2)

    def test_feed_self_link_is_not_the_published_article(self):
        raw = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Introducing GPT-7</title>'
        raw += '<published>2026-11-16</published><link rel="self" href="https://example.org/feed"/>'
        raw += '<link rel="alternate" href="https://example.org/model"/></entry></feed>'
        item, = sources.feed_items(source("openai"), raw)
        self.assertEqual(item["url"], "https://example.org/model")
        with self.assertRaises(ValueError):
            list(sources.feed_items(source("openai"), raw.replace("2026-11-16", "")))

    def test_media_description_does_not_replace_rss_body(self):
        raw = '<rss xmlns:media="http://search.yahoo.com/mrss/"><item>'
        raw += '<title>Gemini Omni: New model</title><media:description>Introducing Gemini Omni</media:description>'
        raw += '<description>Introducing a video generation model.</description><pubDate>2026-11-16</pubDate>'
        raw += '<link>https://example.org/video</link></item></rss>'
        self.assertEqual(list(sources.feed_items(source("gemini"), raw)), [])
        raw = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Release notes</title>'
        raw += '<published>2026-11-16</published><link href="https://example.org/models"/>'
        raw += '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        raw += '<p>Gemini 5 Future is now available.</p></div></content></entry></feed>'
        item, = sources.feed_items(source("vertex"), raw)
        self.assertEqual(item["model"], "Gemini 5 Future")

    def test_numberless_preview_and_restricted_events_keep_their_dates(self):
        base = sources.signal({"id": "claude-news", "org": "Anthropic"}, "a",
                              "Claude Mythos Preview", "https://example.org/preview", "预览",
                              "2026-04-01", model="Claude Mythos Preview")
        self.assertNotEqual(collector.event_id(base), collector.event_id(dict(base, date="2026-06-01")))
        restricted = dict(base, source="openai", kind="受限开放", model="GPT-6 Cyber")
        self.assertNotEqual(collector.event_id(restricted), collector.event_id(dict(restricted, date="2026-06-01")))

    def test_general_launch_matches_same_day_api_but_not_later_access(self):
        item, = feed("Introducing GPT-7")
        record = dict(item, name="GPT-7", kind="开放 API", url="https://example.org/api")
        self.assertTrue(collector.already_catalogued(item, [record]))
        self.assertFalse(collector.already_catalogued(dict(item, date="2026-11-17"), [record]))
        for kind in ["预览", "开放权重"]:
            self.assertFalse(collector.already_catalogued(dict(item, kind=kind), [record]))

    def test_existing_access_stage_is_not_a_duplicate_news_launch(self):
        item, = feed("GPT‑6 Cyber: A new model", day="2026-11-05", body="Introducing GPT‑6 Cyber.")
        record = dict(item, name="GPT-6 Cyber", kind="受限开放", url=item["url"] + "/")
        self.assertTrue(collector.already_catalogued(item, [record]))
        self.assertFalse(collector.already_catalogued(dict(item, date="2026-11-06"), [record]))
        self.assertFalse(collector.already_catalogued(dict(item, model="GPT-6 Cyber Pro"), [record]))

    def test_revision_replays_recent_misses_once_and_keeps_history_quiet(self):
        s = dict(source("openai"), bootstrap_days=3)
        recent, = feed("Introducing GPT-7")
        old, = feed("Introducing GPT-6", day="2026-10-01")
        future, = feed("Introducing GPT-8", day="2026-11-17")
        state = dict(sources={s["id"]: {"checked": "2026-11-15", "seen": [old["id"], recent["id"]]}},
                     pending={}, delivered={})
        snapshot = {r["id"]: r for r in [recent, old, future]}
        self.assertEqual(collector.accept_snapshot(state, s, snapshot, "2026-11-16", []), 1)
        self.assertNotIn(future["id"], state["sources"][s["id"]]["seen"])
        # Serialize/reload as the Actions state branch does before the next run.
        state = json.loads(json.dumps(state))
        self.assertEqual(collector.accept_snapshot(state, s, snapshot, "2026-11-16", []), 0)
        self.assertEqual(collector.accept_snapshot(state, s, snapshot, "2026-11-17", []), 1)

    def test_old_closed_family_receipt_cannot_swallow_new_variant(self):
        item, = feed("Introducing GPT-6.1 Terra")
        old_key = sources.key("OpenAI", "gpt6.1", "发布", item["date"])
        old_issue = dict(number=60, state="closed", comments=0,
                         user={"login": "github-actions[bot]"},
                         body="<!-- atlas-candidate:" + old_key + " -->")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = collector.state_at(path)
            state["delivered"][old_key] = 60
            group = collector.event_id(item)
            state["pending"][group] = {item["id"]: item}
            collector.save(path, state)
            with patch.object(collector, "api", side_effect=[[old_issue], {"number": 61}]) as api:
                collector.deliver(path, "owner/repo", 10)
                self.assertEqual(api.call_count, 2)
            self.assertEqual(collector.state_at(path)["delivered"][group], 61)
            with patch.object(collector, "api") as api:
                collector.deliver(path, "owner/repo", 10)
                api.assert_not_called()

    def test_hub_overlap_inspects_whole_page_before_stopping(self):
        pages = [[dict(id="old", stamp="2026-11-10T00:00:00Z"),
                  dict(id="new", stamp="2026-11-16T00:00:00Z")],
                 [dict(id="next", stamp="2026-11-15T12:00:00Z")]]
        rows = list(recent_models(pages, "stamp", "2026-11-16T00:00:00Z"))
        self.assertEqual([r[0]["id"] for r in rows], ["new", "next"])


class ReleaseNoteTests(unittest.TestCase):
    def test_deepseek_named_siblings(self):
        html = '<article><h2>2025-12-01</h2><h3 id="base">DeepSeek-V3.2</h3>'
        html += '<p>Released.</p><h3 id="speciale">DeepSeek-V3.2-Speciale</h3><p>Released.</p></article>'
        items = page("deepseek", html)
        self.assertEqual([r["model"] for r in items], ["DeepSeek-V3.2", "DeepSeek-V3.2-Speciale"])
        self.assertEqual(len({collector.event_id(r) for r in items}), 2)

    def test_kimi_late_companion_and_preview(self):
        html = '<div class="vp-doc"><h2 id="pro"><span class="ignore-header">K3.1 Pro Series is live</span>'
        html += '<span class="wn-date">October 23, 2026</span></h2><p>K3.1 Pro is now live. '
        html += 'Details. ' * 30 + 'We also launched K3.1 Pro Highspeed, a faster official version.</p></div>'
        items = page("kimi", html)
        self.assertEqual({r["model"] for r in items}, {"Kimi K3.1 Pro", "Kimi K3.1 Pro Highspeed"})
        self.assertEqual({r["date"] for r in items}, {"2026-10-23"})
        future = html.replace("Highspeed", "FutureTier")
        self.assertIn("Kimi K3.1 Pro FutureTier", {r["model"] for r in page("kimi", future)})

    def test_xai_uses_archive_year_rollover_and_ignores_voice(self):
        html = '<main><h2>January</h2><div class="grid"><div>Jan 10</div><div>'
        html += '<h3 id="grok-9">Grok 9</h3><p>Available on the API.</p></div></div>'
        html += '<div class="grid"><div>Jan 9</div><div><h3 id="voice">Grok Voice 9</h3></div></div>'
        html += '<h2>December 2026</h2></main>'
        item, = page("xai", html)
        self.assertEqual((item["date"], item["model"], item["kind"]), ("2027-01-10", "Grok 9", "开放 API"))
        with self.assertRaises(ValueError):
            page("xai", html.replace("December 2026", "December"))

    def test_markdown_release_does_not_import_resales_or_promised_weights(self):
        md = '# Changelog\n\n## 2026-10-06\n\n- Mistral Large 4 (`mistral-large-4`) is now available in Public Preview.\n'
        md += '  - Open weights are coming soon.\n\n- Z.ai GLM (`zai-glm-5`) is now available.\n'
        item, = page("mistral-api", md)
        self.assertEqual((item["model"], item["kind"]), ("mistral-large-4", "预览"))
        md = '# Release notes\n\n## November 12, 2026 {% id="11-12-2026" %}\n'
        md += '* **Gemini 4 Hydrogen (`gemini-4-hydrogen`) GA**: Released Gemini 4 Hydrogen.\n'
        md += '* Released `gemini-omni-1.2-flash`, a video generation model.\n'
        item, = page("gemini-api", md)
        self.assertEqual((item["model"], item["kind"]), ("gemini-4-hydrogen", "发布"))

    def test_longcat_preview_and_formal(self):
        html = '<main><h2>版本: 2026-11-02</h2><h3 id="final">LongCat-2.5 上线</h3>'
        html += '<h2>版本: 2026-09-25</h2><h3 id="preview">LongCat-2.5-Preview 上线</h3></main>'
        items = page("longcat-api", html)
        self.assertEqual([r["kind"] for r in items], ["发布", "预览"])

    def test_doubao_month_precision_and_two_updates_of_same_alias(self):
        def table(note, owner="字节跳动", name="doubao-seed-evolving", kind="深度思考模型"):
            headers = ["提供方", "模型 ID", "模型类型", "版本说明", "其它说明"]
            return '<table><tr>' + ''.join('<th>'+v+'</th>' for v in headers) + '</tr><tr>' + ''.join(
                '<td>'+v+'</td>' for v in [owner, name, kind, "更新", note]) + '</tr></table>'
        html = '<h2 id="202611">202611</h2>' + table("模型能力更新 A") + table("模型能力更新 B")
        html += table("转售", "深度求索", "deepseek-v4") + table("视频", kind="视频生成模型")
        items = page("doubao-api", html)
        self.assertEqual(len(items), 2)
        self.assertEqual({r["date"] for r in items}, {"2026-11"})
        self.assertEqual(len({collector.event_id(r) for r in items}), 2)
        state = dict(sources={}, pending={})
        self.assertEqual(collector.accept_snapshot(state, source("doubao-api"),
                         {r["id"]: r for r in items}, "2026-11-16", []), 2)

    def test_minimax_uses_local_display_date_and_language_family(self):
        html = '<a href="/blog/new"><article><h2>MiniMax M4: A new language model</h2>'
        html += '<time datetime="2026-11-15T16:23:20Z">2026-11-16</time></article></a>'
        html += '<a href="/blog/video"><article><h2>MiniMax H3: A video model</h2>'
        html += '<time datetime="2026-11-15T16:23:20Z">2026-11-16</time></article></a>'
        item, = page("minimax-news", html)
        self.assertEqual((item["model"], item["date"]), ("MiniMax M4", "2026-11-16"))

    def test_missing_publisher_structure_does_not_succeed_as_empty(self):
        for sid in ["deepseek", "xai", "kimi", "mercury", "longcat-api", "mimo-api",
                    "zai-api", "cohere-api", "gemini-api", "mistral-api", "minimax-news", "doubao-api"]:
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                page(sid, '<html><h1>Temporary outage</h1></html>')
