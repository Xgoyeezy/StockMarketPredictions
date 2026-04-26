import CustomMarketChart from './CustomMarketChart'

export default function CandlestickChart({
  payload,
  ticker = '',
  interval = '5m',
  height = 560,
  autoRefreshLabel = 'Chart snapshot',
  ...rest
}) {
  return (
    <CustomMarketChart
      payload={payload}
      ticker={ticker || payload?.ticker || ''}
      interval={interval || payload?.interval || '5m'}
      height={height}
      autoRefreshLabel={autoRefreshLabel}
      {...rest}
    />
  )
}
