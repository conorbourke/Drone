/**
 * Small inline flag for a supplier's country (Ireland or the United Kingdom) with the country
 * code beside it, so the country never depends on the picture alone.
 */
export function CountryFlag({ country }: { country: 'IE' | 'UK' | string }) {
  const name = country === 'IE' ? 'Ireland' : country === 'UK' ? 'United Kingdom' : country;
  return (
    <span className="country-flag" data-testid="parts-supplier-country" data-country={country} title={name}>
      {country === 'IE' ? (
        <svg viewBox="0 0 30 20" width="18" height="12" aria-hidden="true" focusable="false">
          <rect width="10" height="20" x="0" fill="#169b62" />
          <rect width="10" height="20" x="10" fill="#ffffff" />
          <rect width="10" height="20" x="20" fill="#ff883e" />
          <rect width="30" height="20" fill="none" stroke="rgba(0,0,0,0.25)" strokeWidth="1" />
        </svg>
      ) : country === 'UK' ? (
        <svg viewBox="0 0 60 30" width="18" height="12" aria-hidden="true" focusable="false">
          <g>
            <rect width="60" height="30" fill="#012169" />
            <path d="M0,0 L60,30 M60,0 L0,30" stroke="#ffffff" strokeWidth="6" />
            <path d="M0,0 L60,30 M60,0 L0,30" stroke="#c8102e" strokeWidth="2" />
            <path d="M30,0 V30 M0,15 H60" stroke="#ffffff" strokeWidth="10" />
            <path d="M30,0 V30 M0,15 H60" stroke="#c8102e" strokeWidth="6" />
          </g>
        </svg>
      ) : null}
      <span className="country-code">{country}</span>
    </span>
  );
}
