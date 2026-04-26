import { useEffect, useState } from 'react'
import { clearRecentTickers, getTickerHub, toggleFavoriteTicker } from '../api/client'
import Button from './Button'
import Chip from './Chip'

export default function TickerHub({
  activeTicker = '',
  onSelectTicker,
  onLoadFavorites,
  compact = false,
  onDataChange,
}) {
  const [hub, setHub] = useState({ favorites: [], recent: [] })
  const [loading, setLoading] = useState(true)

  async function loadHub() {
    try {
      setLoading(true)
      const data = await getTickerHub(compact ? 6 : 10)
      setHub(data)
      onDataChange?.(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadHub()
  }, [])

  async function handleFavorite(ticker) {
    const data = await toggleFavoriteTicker(ticker)
    setHub(data)
    onDataChange?.(data)
  }

  async function handleClearRecent() {
    const data = await clearRecentTickers()
    setHub(data)
    onDataChange?.(data)
  }

  return (
    <div className={`ticker-hub ${compact ? 'ticker-hub--compact' : ''}`}>
      <div className="ticker-hub__section">
        <div className="ticker-hub__header">
          <strong>Favorites</strong>
          {onLoadFavorites ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onLoadFavorites(hub.favorites)}
            >
              Use favorites
            </Button>
          ) : null}
        </div>
        <div className="ticker-chip-row">
          {(hub.favorites || []).length ? (
            hub.favorites.map((ticker) => (
              <div
                key={`fav-${ticker}`}
                className={`ticker-chip ${
                  String(activeTicker).toUpperCase() === ticker ? 'ticker-chip--active' : ''
                }`}
              >
                <Chip
                  as="button"
                  type="button"
                  tone="neutral"
                  size="sm"
                  active={String(activeTicker).toUpperCase() === ticker}
                  className="ticker-chip__label"
                  onClick={() => onSelectTicker?.(ticker)}
                >
                  {ticker}
                </Chip>
                <Chip
                  as="button"
                  type="button"
                  tone="neutral"
                  size="sm"
                  className="ticker-chip__star"
                  onClick={() => handleFavorite(ticker)}
                  title="Remove favorite"
                >
                  Fav
                </Chip>
              </div>
            ))
          ) : (
            <span className="ui-empty ticker-hub__empty">No favorites yet.</span>
          )}
        </div>
      </div>

      <div className="ticker-hub__section">
        <div className="ticker-hub__header">
          <strong>Recent</strong>
          <Button type="button" variant="ghost" size="sm" onClick={handleClearRecent}>
            Clear
          </Button>
        </div>
        <div className="ticker-chip-row">
          {loading ? (
            <span className="ui-empty ticker-hub__empty">Loading...</span>
          ) : (hub.recent || []).length ? (
            hub.recent.map((ticker) => (
              <div
                key={`recent-${ticker}`}
                className={`ticker-chip ${
                  String(activeTicker).toUpperCase() === ticker ? 'ticker-chip--active' : ''
                }`}
              >
                <Chip
                  as="button"
                  type="button"
                  tone="neutral"
                  size="sm"
                  active={String(activeTicker).toUpperCase() === ticker}
                  className="ticker-chip__label"
                  onClick={() => onSelectTicker?.(ticker)}
                >
                  {ticker}
                </Chip>
                <Chip
                  as="button"
                  type="button"
                  tone="neutral"
                  size="sm"
                  className="ticker-chip__star ticker-chip__star--muted"
                  onClick={() => handleFavorite(ticker)}
                  title="Add favorite"
                >
                  Add
                </Chip>
              </div>
            ))
          ) : (
            <span className="ui-empty ticker-hub__empty">No recent tickers yet.</span>
          )}
        </div>
      </div>
    </div>
  )
}
