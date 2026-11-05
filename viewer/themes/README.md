# Themes

`viewer.css` defines all colours and sizes as CSS custom properties on `:root`.
`index.html` also loads `themes/theme-local.css` after it, if present. That file is
yours and is gitignored, so put any overrides there:

```css
:root {
  --accent: #3a76f0;
  --bg: #101418;
}
```

No theme ships with this repo. A missing `theme-local.css` is fine and the default look is used.
