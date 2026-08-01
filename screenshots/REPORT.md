# Dok.Moscow - Website Testing Report
Date: August 1, 2026

## Summary
All pages load successfully with NO errors. Both landing page and login page display properly with styled backgrounds and working CSS.

---

## 1. https://dok.moscow/ (Landing Page)

### Visual Content:
- ✅ **Page loads successfully**
- Header with logo "Док.Москва" and navigation (Примеры, Тарифы, Доступ, Войти)
- Main heading: "Док.Москва"
- Subtitle: "Кабинет экспертной организации"
- Descriptive text about contracts and EGRIOP
- Two CTA buttons: "Запросить ранний доступ" and "Войти в кабинет"
- Left side: animated folder/document illustration
- **Background**: Dark gradient (teal/green/brown mix) - NOT black or empty

### Console (F12):
- ✅ **No issues** - Console is clean, no errors

### Network Tab:
- ✅ **landing.css** - Status 200 OK
- URL: https://dok.moscow/static/landing.css
- Content-Type: text/css; charset=utf-8
- File contains CSS with Russian comment: "/* Лендинг Док.Москва — отдельная композиция от кабинета */"
- All resources loaded successfully (fonts, CSS, etc.)

### Background Color (Computed):
- CSS variable: `background: var(--lp-paper);`
- Computed value: **rgb(242, 235, 225)** (light cream/beige)
- Visual appearance: Dark gradient overlay creates the teal/green look

### DOM:
- ✅ Full HTML structure present
- body.lp with proper content
- All elements rendering correctly

---

## 2. https://app.dok.moscow/login (Login Page)

### Visual Content:
- ✅ **Page loads successfully**
- Title: "Doc.Moscow" (English version via Google Translate)
- Subtitle: "Expert organization's office"
- Login form with Email and Password fields
- "Login" button and "Forgot your password?" link
- **Background**: Dark teal/green gradient (same as landing page)

### Console (F12):
- ✅ **No issues** - Console is clean, no errors

### Network Tab:
- ✅ **app.css** - Status 304 Not Modified
- URL: https://app.dok.moscow/static/app.css
- Successfully loaded and cached
- Other resources:
  - login (document) - 200
  - htmx.org@2.0.4 - 301
  - htmx.min.js - 200
  - Google Fonts - all 200
  - Translation resources - 200

### Background:
- Similar gradient styling to landing page
- No black/empty screen issues

---

## 3. https://dok.moscow/static/landing.css (Direct CSS File)

### Response:
- ✅ **Status 200 OK**
- Content-Type: text/css
- **File content visible** - CSS text successfully returned
- Contains CSS variables and styling rules
- File size: ~10.5 KB

### Sample Content:
```css
/* Лендинг Док.Москва — отдельная композиция от кабинета */
:root {
  --lp-ink: #10241f;
  --lp-taper: #f2ebe1;
  --lp-muted: #4a635b;
  --lp-accent: #0d6e65;
  ...
}
```

---

## Overall Findings:

### ✅ All Systems Working:
1. **Both pages load and display correctly**
2. **All CSS files load with 200/304 status**
3. **No console errors on any page**
4. **No 404 errors for CSS/JS files**
5. **DOM contains full HTML structure**
6. **Pages are NOT black/empty - they have proper styled backgrounds**

### Background Analysis:
- The pages use CSS variables (--lp-paper, etc.)
- Computed background-color is light cream (rgb(242, 235, 225))
- Visual appearance shows dark gradient due to CSS gradient overlays
- Background appears as dark teal/green/brown gradient mixture
- This is intentional design, NOT a loading failure

### No Issues Found:
- No network failures
- No missing static files
- No JavaScript errors
- No CSS loading problems
- Pages fully functional

---

## Screenshots Saved:
1. 1-dok-moscow-landing.webp - Landing page clean view
2. 2-app-dok-moscow-login.webp - Login page clean view
3. 3-landing-css-content.webp - Direct CSS file content
4. 4-landing-network-devtools.webp - Landing page Network tab
5. 5-login-network-devtools.webp - Login page Network tab
6. 6-landing-computed-styles.webp - Computed background color

---

## Conclusion:
**All pages are working perfectly. No errors, no missing files, no black screens.**
The dark gradient background is intentional design, not a problem.
