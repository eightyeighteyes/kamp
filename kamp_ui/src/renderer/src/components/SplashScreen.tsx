import React from 'react'

export function SplashScreen({ hiding }: { hiding: boolean }): React.JSX.Element {
  return (
    <div className={`splash-screen${hiding ? ' splash-hiding' : ''}`}>
      <svg
        className="splash-svg"
        viewBox="0 0 230 200"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        {/* Decorative stars — twinkle independently */}
        <text
          className="splash-deco s1"
          x="22"
          y="38"
          textAnchor="middle"
          fontSize="11"
          fill="#c4aa78"
        >
          ✦
        </text>
        <text
          className="splash-deco s2"
          x="20"
          y="162"
          textAnchor="middle"
          fontSize="8"
          fill="#c4aa78"
        >
          ✦
        </text>
        <text
          className="splash-deco s3"
          x="180"
          y="170"
          textAnchor="middle"
          fontSize="7"
          fill="#c4aa78"
        >
          ✧
        </text>
        <text
          className="splash-deco s4"
          x="162"
          y="12"
          textAnchor="middle"
          fontSize="7"
          fill="#c4aa78"
        >
          ✧
        </text>

        {/* Music notes — float gently */}
        <text className="splash-note n1" x="10" y="105" fontSize="15" fill="#c4aa78">
          ♪
        </text>
        <text className="splash-note n2" x="196" y="130" fontSize="12" fill="#c4aa78">
          ♫
        </text>

        {/* === Spinning record === */}
        <g className="splash-record-spin">
          {/* Record body */}
          <circle cx="100" cy="100" r="86" fill="#1c1a16" stroke="#c4aa78" strokeWidth="1.5" />
          {/* Pressed grooves — very subtle concentric rings */}
          {[78, 70, 62, 54, 46, 38, 32].map((r) => (
            <circle
              key={r}
              cx="100"
              cy="100"
              r={r}
              fill="none"
              stroke="#2a2620"
              strokeWidth="0.8"
            />
          ))}
          {/* Center label */}
          <circle cx="100" cy="100" r="26" fill="#bf7a20" />
          <circle
            cx="100"
            cy="100"
            r="22.5"
            fill="none"
            stroke="#8a5515"
            strokeWidth="0.6"
            strokeDasharray="2.5 2"
          />
          <circle cx="100" cy="100" r="26" fill="none" stroke="#8a5515" strokeWidth="1" />
          {/* Label text */}
          <text
            x="100"
            y="97"
            textAnchor="middle"
            fill="#1c1a16"
            fontSize="9"
            fontWeight="700"
            letterSpacing="2.5"
            fontFamily="'DM Sans', sans-serif"
          >
            KAMP
          </text>
          <text
            x="100"
            y="108"
            textAnchor="middle"
            fill="#1c1a16"
            fontSize="4.8"
            letterSpacing="1.5"
            fontFamily="'DM Sans', sans-serif"
          >
            HI · FI
          </text>
          {/* Centre spindle hole */}
          <circle cx="100" cy="100" r="3.5" fill="#141414" />
        </g>

        {/* === Tonearm (static) === */}
        {/* Pivot post — arm connects to its centre */}
        <circle cx="218" cy="18" r="7" fill="none" stroke="#c4aa78" strokeWidth="1.5" />
        <circle cx="218" cy="18" r="2.5" fill="#c4aa78" />
        {/*
          Arm geometry (angles clockwise from 12 o'clock):
          - Main tube exits pivot centre at 195° for 140 px → bend at (182, 153)
          - Headshell continues from bend to stylus at 170° on groove r=74 → (113, 173)
        */}
        <path
          d="M 218 18 L 187 134 Q 182 153 113 173"
          fill="none"
          stroke="#c4aa78"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* Stylus head */}
        <circle cx="113" cy="173" r="3" fill="none" stroke="#c4aa78" strokeWidth="1.2" />
        <circle cx="113" cy="173" r="1.5" fill="#c4aa78" />
      </svg>
    </div>
  )
}
