# felipebalbi.github.io

Source for [balbi.sh](https://balbi.sh), built with [Zola](https://www.getzola.org/) and styled after [ef-melissa-light](https://github.com/protesilaos/ef-themes).

## Layout

```
config.toml           Zola config
content/              Markdown sources for pages and posts
sass/                 SCSS that compiles to /style.css
templates/            Tera templates (base, index, section, page, tags, 404)
static/               Files copied verbatim to site root (fonts, etc.)
.github/workflows/    CI: build on push to main, deploy to gh-pages
```

## How it deploys

- **`main`** holds the source. Never served to visitors.
- **`gh-pages`** holds the built output. GitHub Pages serves this branch.
- A push to `main` triggers `.github/workflows/deploy.yml`, which:
  1. Installs Zola.
  2. Runs `zola build` (output goes to `public/`).
  3. Pulls `pico-de-gallo/`, `CNAME`, and `.nojekyll` from the previous `gh-pages` tip.
  4. Force-pushes the merged result to `gh-pages`.

The `pico-de-gallo/` directory is published into this site by a workflow in
the [pico-de-gallo](https://github.com/OpenDevicePartnership/pico-de-gallo)
repository. The deploy preserves it untouched.

## One-time setup

After this branch is merged to `main`:

1. Push to `main`. The workflow will create `gh-pages` on its first run.
2. **Repo Settings → Pages → Build and deployment → Source**: switch from
   "Deploy from a branch: main" to "Deploy from a branch: gh-pages /(root)".
3. CNAME (`balbi.sh`) is preserved by the workflow on every deploy.

## Local preview

```sh
zola serve     # http://127.0.0.1:1111
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

The site self-hosts six woff2 files (Aporetic Serif regular/italic/bold,
Aporetic Sans regular/bold, Aporetic Sans Mono regular) totalling ~660 KB.
These are subsetted from the original TTFs to Latin + common symbols.

## License

Site content: CC BY 4.0.
Source code (templates, sass, build scripts): MIT.
