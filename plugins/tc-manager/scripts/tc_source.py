"""Create a plain-text working copy of Zephyr rich-text JSON; keep the source intact."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re


class SourceText(HTMLParser):
    blocks = {"p", "div", "section", "blockquote", "h1", "h2", "h3", "tr", "pre"}
    styles = {"span", "strong", "b", "em", "i", "u", "s", "code", "table", "tbody", "thead"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.lists = [], [], []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.blocks or tag == "br":
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.lists.append(0 if tag == "ol" else None)
            self.parts.append("\n")
        elif tag == "li":
            prefix = "- "
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1
                prefix = str(self.lists[-1]) + ". "
            self.parts.append("\n" + prefix)
        elif tag in {"td", "th"}:
            self.parts.append("\t")
        elif tag == "a":
            self.links.append(attrs.get("href", ""))
        elif tag == "img":
            self.parts.append("[image: " + " ".join(filter(None, [attrs.get("alt"), attrs.get("src")])) + "]")
        elif tag not in self.styles:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in self.blocks or tag == "li":
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
            self.parts.append("\n")
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(" (" + href + ")")
        elif tag not in self.styles | {"br", "td", "th", "img", "a"}:
            self.parts.append("</" + tag + ">")


def text(value):
    parser = SourceText()
    parser.feed(value)
    parser.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def normalize(value):
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for name, item in value.items():
        if name in {"objective", "precondition", "description", "testData", "expectedResult"} and isinstance(item, str):
            result[name] = text(item)
        else:
            result[name] = normalize(item)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.source.read_text(encoding="utf-8-sig"))
    # Exclusive creation also prevents replacing the raw input or an earlier projection.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(normalize(data), stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
