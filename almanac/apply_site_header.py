"""Copies the website's header (menu, search icon, Subscribe, light/dark button, pinned header) into the
Almanac templates, using the website's own tool so the Almanac always matches tallorderdigital.com.

    python3 almanac/apply_site_header.py <path to tallorderdigital-site>

Run it whenever the website's menu or header changes (the site repo's CLAUDE.md says when), then open a PR.
Why a wrapper: the Almanac lives on the weather subdomain, so its links to the main site are absolute
({{MAIN}} in the templates, filled in by build.py), and the site tool expects relative links. This swaps
{{MAIN}} for a stand-in, runs the tool, marks "Weather" as the current menu item, and swaps it back.
"""
import importlib.util, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATES = [HERE / "template.html", HERE / "index_template.html"]
STAND_IN = "__MAIN__/"

# One-time cleanup (Nov 9, 2026): the Almanac used to carry its own copy of the light/dark button code. The site
# tool now writes the shared copy, and two copies would each flip the theme on one click (so nothing would change).
OLD_TOGGLE_CSS = re.compile(r"^\.theme-toggle[^\n]*\n(?:  [^\n]*\n)?", re.M)
OLD_TOGGLE_JS = re.compile(r"\n/\* theme toggle \(same behavior as [^)]*\) \*/\n\(function \(\) \{.*?\n\}\)\(\);\n", re.S)
CURRENT = '.site-nav a[aria-current="page"] { color: var(--ink); box-shadow: inset 0 -2px 0 var(--amber); padding-bottom: 4px; }\n'


def main(site_repo):
    spec = importlib.util.spec_from_file_location("apply_header", pathlib.Path(site_repo) / "tools" / "apply-header.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    for path in TEMPLATES:
        html = path.read_text(encoding="utf-8")
        old = html
        if "/* theme-toggle:start */" not in html:            # first run only
            html = OLD_TOGGLE_JS.sub("\n", html, count=1)
            head, rest = html.split("<header", 1)
            html = OLD_TOGGLE_CSS.sub("", head) + "<header" + rest
        if '.site-nav a[aria-current="page"]' not in html:
            html = html.replace(".brand {", CURRENT + ".brand {", 1)
        html = html.replace("{{MAIN}}", STAND_IN)
        html = tool.apply(html, path)
        html = html.replace(f'<a href="{STAND_IN}weather/">', f'<a href="{STAND_IN}weather/" aria-current="page">', 1)
        html = html.replace(STAND_IN, "{{MAIN}}")
        if html != old:
            path.write_text(html, encoding="utf-8")
            print("updated:", path.relative_to(HERE.parent))
        else:
            print("already current:", path.relative_to(HERE.parent))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
