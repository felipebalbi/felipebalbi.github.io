# felipebalbi.github.io

Source for [balbi.sh](https://balbi.sh), built with
[Zola](https://www.getzola.org/) and styled with a custom theme
inspired by [ef-melissa-light / ef-melissa-dark](https://github.com/protesilaos/ef-themes).

## Layout

```
config.toml             Zola config (activates theme = "ef-melissa")
content/                Markdown sources for pages and posts
static/                 Site-specific files copied to /  (CNAME)
themes/ef-melissa/      The site's theme. Contains:
  theme.toml              theme metadata
  sass/                   palette + layout + syntax CSS
  templates/              base, index, section, page, tags, 404, atom.xml
  static/fonts/           self-hosted Aporetic woff2 files
.github/workflows/      CI: build on push to main, deploy to gh-pages
```

The repo intentionally keeps **only site-specific content** at
the root. All styling, templates, and fonts live under
`themes/ef-melissa/`, which is structured as a stand-alone Zola
theme. Anything the site needs to override (custom templates,
extra static files) can be added at the root and will shadow
the theme.

## Theme: ef-melissa

A direct port of Protesilaos Stavrou's
[ef-melissa-light / ef-melissa-dark](https://github.com/protesilaos/ef-themes)
Emacs themes. Warm, generous in whitespace, serif body /
sans UI / mono code (all three from the Aporetic family,
also by Protesilaos).

### Light / dark switching

The theme ships both palettes. Every page load starts in
"auto" mode: a small inline `<script>` in `<head>` resolves
the OS `prefers-color-scheme` to either `light` or `dark`
and sets `data-theme` on `<html>` *before* paint, so there's
no flash of wrong colors. A two-state button in the header
flips between light and dark for the current page. Nothing
is persisted — a reload returns to the OS preference.

Syntax highlighting uses Zola's class-based mode, so both
`giallo-light.css` and `giallo-dark.css` are linked at all
times; the inactive one is `disabled` based on `data-theme`.

## How it deploys

- **`main`** holds the source plus two legacy paths managed externally:
  - `pico-de-gallo/` — published into `main` by a workflow in the
    [pico-de-gallo](https://github.com/OpenDevicePartnership/pico-de-gallo)
    repo. We treat `main` as source-of-truth for it.
  - `CNAME` — kept at the repo root *and* in `static/` so Zola publishes it
    too. The workflow copies it explicitly as well.
- **`gh-pages`** holds the built output. GitHub Pages serves this branch.
- A push to `main` triggers `.github/workflows/deploy.yml`, which:
  1. Checks out `main`.
  2. Installs Zola and runs `zola build` (output goes to `public/`).
  3. Layers `pico-de-gallo/`, `CNAME`, and `.nojekyll` from `main` on top
     of the Zola output.
  4. Force-pushes `public/` to `gh-pages` (orphan history, one commit
     per deploy).

Two workflow runs per push are normal:
1. **Build and deploy site** (this repo) — turns `main` into
   `gh-pages`.
2. **pages build and deployment** (GitHub-built-in) — publishes
   `gh-pages` to the CDN.

## Local preview

```sh
zola serve     # http://127.0.0.1:1111  (live reload)
zola build     # writes ./public (gitignored)
```

## Writing a post

Create `content/posts/YYYY-MM-DD-slug.md`:

```markdown
+++
title = "Post title"
date = 2026-06-10
description = "One-line summary used in OG tags and feeds."
[taxonomies]
tags = ["rust", "embedded"]
+++

Body in Markdown.
```

Push to `main` and the site rebuilds.

## Fonts

The theme self-hosts six woff2 files (Aporetic Serif
regular/italic/bold, Aporetic Sans regular/bold, Aporetic
Sans Mono regular) totalling ~660 KB. These are subsetted
from the original TTFs to Latin + common symbols.

## License

Site content: CC BY 4.0.
Source code (templates, sass, build scripts): MIT.
