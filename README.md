# Newston

Newston is a static GitHub Pages website for the official student-led newspaper of Newton College.

The homepage is an About Us page, and each article section is generated from PDFs or Word documents placed in the matching folder:

- `school-community/`
- `sports/`
- `global-issues-culture/`

## Add a New Article

1. Export the article as a PDF or Word `.docx` file.
2. Drop the file into the correct folder:
   - School Community: `school-community/`
   - Sports: `sports/`
   - Global Issues & Culture: `global-issues-culture/`
3. Commit and push to `main`.
4. GitHub Actions rebuilds the site and deploys it to GitHub Pages.

During the build, `scripts/build_site.py` automatically:

- detects new PDFs and `.docx` files,
- extracts the title, author, article text, and first usable image,
- renders a fallback cover from the first PDF page or creates a Newston-style cover for Word documents with no image,
- generates a full article page,
- adds the article card to the correct section page.

For best automatic extraction, format articles with the title near the top and a byline such as `By Student Name` or `Por Student Name` below it.

## Local Preview

Build the site:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/build_site.py
```

Serve it locally:

```bash
python3 -m http.server 4321 -d dist
```

Then open `http://localhost:4321`.

If there are no PDFs yet, the build still works and shows empty article states.

## GitHub Pages Deployment

1. Push this repository to GitHub.
2. In GitHub, open **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **GitHub Actions**.
4. Push to the `main` branch.
5. The workflow in `.github/workflows/deploy.yml` builds `dist/` and publishes it.

If the website opens this README instead of the Newston homepage, GitHub Pages is using the wrong source. Go back to **Settings → Pages** and change **Source** from **Deploy from a branch** to **GitHub Actions**, then rerun the deploy workflow from the **Actions** tab.

## Branding Assets

The site uses the provided Newston newspaper logo and Newton College logo:

- Newston logo: `public/assets/brand/newston-wordmark-logo.png`
- Newton College logo: `public/assets/brand/newton-college-logo.png`
- Team portraits: `public/assets/team/`

To update footer links, edit these fields in `src/site_config.json`:

- `instagramUrl`
- `joinUrl`
