UI art direction pack

What this pack gives you
- premium dark fintech color system
- calmer spacing and hierarchy
- softer cards, less border noise
- better chart-first layout styling
- entrance animation utilities
- scroll reveal utility for long pages

Files included
- frontend/src/styles.css
- frontend/src/components/PageTransition.jsx
- frontend/src/components/RevealSection.jsx

Recommended next implementation steps
1. Replace your current frontend/src/styles.css with the one in this pack.
2. Install Framer Motion:
   npm install framer-motion
3. Wrap page content in PageTransition.
4. Use RevealSection around major sections:
   - hero
   - metrics row
   - chart/analysis section
   - tables and monitor panels

Design direction
- base: deep navy, not black
- primary accent: indigo blue
- secondary accent: cyan
- green/red only for outcomes
- softer glass surfaces, fewer heavy borders
- more whitespace and stronger text hierarchy

Suggested usage examples

Dashboard page:
<PageTransition>
  <section className="hero fade-up">...</section>
  <RevealSection><section className="metrics-grid">...</section></RevealSection>
  <RevealSection><section className="content-grid--wide">...</section></RevealSection>
</PageTransition>

Notes / Alerts / Portfolio:
- keep one hero or heading row
- one primary action area
- one main content region
- put advanced filters in a collapsible area

Best next visual pass after this
- simplify the topbar further
- reduce chip count above the fold
- make the chart region taller
- convert crowded data bars into grouped sections