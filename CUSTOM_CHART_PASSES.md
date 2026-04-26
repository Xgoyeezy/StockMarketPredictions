# Custom Chart Passes

This is the full pass-by-pass build order for the proprietary chart engine.

Current status:
- Pass 1 complete
- Pass 2 complete
- Pass 3 complete
- Pass 4 complete
- Pass 5 complete
- Pass 6 complete
- Pass 7 complete
- Pass 8 complete
- Pass 9 complete
- Pass 10 complete
- Pass 11 complete
- Pass 12 complete
- Pass 13 complete
- Pass 14 complete
- Pass 15 complete
- Pass 16 complete
- Pass 17 complete
- Pass 18 complete
- Pass 19 complete

Milestone status:
- Milestone 1 complete
- Milestone 2 complete
- Milestone 3 complete
- Milestone 4 complete
- Milestone 5 complete
- Milestone 6 complete

## Pass 1 - Milestone 1 foundation
Status: complete

Goals:
- Create chart-engine core folders and modules
- Add single-pane viewport, time scale, and price scale math
- Render a custom canvas price chart with candles and line mode
- Render background, grid, time axis, and price axis
- Keep the existing desk shell around the new chart
- Support empty/loading/error states

Exit:
- The app uses our own price-pane renderer instead of a charting library for the main pane

## Pass 2 - Milestone 2 interaction basics
Status: complete

Goals:
- Add drag panning
- Add wheel zoom
- Add crosshair
- Add double-click reset
- Add recent high/low markers
- Add latest-bar emphasis
- Add current-price label polish
- Improve hover updates with requestAnimationFrame

Exit:
- The custom chart feels interactive and readable, not just static

## Pass 3 - Milestone 2 viewport polish
Status: complete

Goals:
- Tighten cursor-centered zoom math
- Add better future-space behavior on the right
- Improve price auto-fit rules for visible candles only
- Add explicit reset-scale control in the chart shell
- Separate reset-time-range from reset-price-scale behavior
- Clamp extreme zoom states more cleanly

Exit:
- Time and price viewport behavior feels deliberate and predictable

## Pass 4 - Milestone 2 scale and readout polish
Status: complete

Goals:
- Add adaptive time-axis label density
- Add adaptive price-axis tick density
- Improve right-edge live price label behavior
- Add cleaner top-left hover readout formatting
- Improve latest candle emphasis
- Add more natural current-price guide styling

Exit:
- The custom chart reads more like a real trading platform

## Pass 5 - Milestone 2 session and market-context pass
Status: complete

Goals:
- Add premarket / regular / after-hours shading
- Add session separators and session labels
- Add visible stale-feed / delayed-data cues
- Improve symbol metadata strip alignment with the chart state

Exit:
- A user can read both price action and session context directly from the chart

## Pass 6 - Milestone 3 volume pane
Status: complete

Goals:
- Add a real linked volume pane below price
- Render volume bars from the same time scale
- Add volume moving average line
- Add volume last-value label
- Persist pane visibility

Exit:
- Price and volume behave like separate synchronized panes

## Pass 7 - Milestone 3 RSI pane
Status: complete

Goals:
- Add RSI pane with its own vertical scale
- Add 30 / 50 / 70 guide levels
- Add RSI last-value label
- Sync crosshair between price and RSI
- Persist RSI pane visibility

Exit:
- RSI behaves like a proper lower study pane

## Pass 8 - Milestone 3 MACD pane
Status: complete

Goals:
- Add MACD line
- Add signal line
- Add histogram
- Add MACD last-value labels
- Sync crosshair between all panes
- Persist MACD pane visibility

Exit:
- MACD behaves like a proper lower study pane

## Pass 9 - Milestone 3 pane management
Status: complete

Goals:
- Add pane height ratios
- Add pane resize handles
- Persist pane heights
- Add pane headers with compact live values
- Add shared x-axis behavior across all panes

Exit:
- Price, volume, RSI, and MACD feel like one linked chart stack

## Pass 10 - Milestone 4 overlay framework
Status: complete

Goals:
- Add overlay renderer for EMA / SMA / VWAP
- Add overlay visibility toggles
- Add overlay last-value labels
- Add overlay color system and layering rules
- Add overlay persistence

Exit:
- Core study overlays live on the custom engine

## Pass 11 - Milestone 4 trading markers
Status: complete

Goals:
- Add entry markers
- Add stop markers
- Add target markers
- Add working-order markers
- Add pending-order labels
- Add open-position markers
- Add better price-line collision handling

Exit:
- Trading context is visible directly on the custom chart

## Pass 12 - Milestone 4 order and session polish
Status: complete

Goals:
- Add time-in-force badges for working orders
- Add order-type labels
- Add session VWAP / key guide rendering
- Add richer order and position right-edge labels

Exit:
- The chart can visually explain trading state without relying on the side rail

## Pass 13 - Milestone 5 layout persistence
Status: complete

Goals:
- Save full chart state:
  - viewport
  - pane sizes
  - chart style
  - hidden overlays
  - shown panes
  - guide objects
- Add layout reset
- Add safer restore behavior

Exit:
- The chart reliably comes back exactly as the user left it

## Pass 14 - Milestone 5 performance layering
Status: complete

Goals:
- Split static and dynamic canvas layers
- Cache text measurements
- Only draw visible bars and labels
- Reduce redraw cost during hover and pan
- Profile hot paths

Exit:
- The chart stays smooth under heavier data and more overlays

## Pass 15 - Milestone 5 device and reliability pass
Status: complete

Goals:
- Improve touch interactions
- Improve mobile resizing
- Harden empty/error/stale states
- Add safer loading transitions between symbols and intervals
- Add internal engine tests for viewport math

Exit:
- The chart is reliable enough for daily use across desktop and mobile

## Pass 16 - Milestone 6 drawing model
Status: complete

Goals:
- Add drawing object schema
- Add drawing layer
- Add trendline tool
- Add horizontal line tool
- Add rectangle / zone tool
- Add text / note marker tool

Exit:
- The engine supports authored drawings, not just price data

## Pass 17 - Milestone 6 drawing interaction
Status: complete

Goals:
- Add select / drag / resize behavior for drawings
- Add hit-testing
- Add lock / unlock
- Add delete
- Add persistence for drawings

Exit:
- Drawings behave like first-class chart objects

## Pass 18 - Milestone 6 history and editing
Status: complete

Goals:
- Add undo / redo
- Add object context actions
- Add snapping rules
- Add drawing toolbar state
- Add cleaner selection visuals

Exit:
- Drawings feel usable, not experimental

## Pass 19 - Milestone 6 advanced tools
Status: complete

Goals:
- Add ray / extended-line support
- Add magnet / snap mode
- Add grouped visibility for drawings
- Add alert-anchor-ready object ids

Exit:
- The chart starts to become a real proprietary workstation surface

## Pass 20 - Cutover and cleanup
Status: queued

Goals:
- Remove the legacy chart dependency path
- Remove dead chart wrapper code
- Trim unused chart-library packages when safe
- Update docs to point to the proprietary engine

Exit:
- The desk is fully running on the custom chart engine

## Recommended execution order from here
1. Pass 3
2. Pass 4
3. Pass 5
4. Pass 6
5. Pass 7
6. Pass 8
7. Pass 9
8. Pass 10
9. Pass 11
10. Pass 12
11. Pass 13
12. Pass 14
13. Pass 15
14. Pass 16
15. Pass 17
16. Pass 18
17. Pass 19
18. Pass 20
