import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

interface CardProps {
  title: string;
  subtitle?: string;
  badge?: string;
  badgeColor?: string;
  badgeTextColor?: string;
  borderColor?: string;
  backgroundColor?: string;
  plotType?: 'a-plot' | 'b-plot';
  children?: React.ReactNode;
  delay?: number;
}

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  badge,
  badgeColor = '#EFF6FF',
  badgeTextColor = '#1D4ED8',
  borderColor = '#E2E8F0',
  backgroundColor = '#F8FAFC',
  plotType,
  children,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = spring({
    frame: Math.max(0, frame - delay),
    fps,
    config: { damping: 14, mass: 0.6 },
  });

  const opacity = interpolate(frame - delay, [0, 10], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        background: backgroundColor,
        borderRadius: 16,
        border: `1.5px solid ${borderColor}`,
        padding: '30px 36px',
        color: '#0F172A',
        fontFamily: 'Inter, sans-serif',
        opacity,
        transform: `translateY(${(1 - entrance) * 30}px)`,
        display: 'flex',
        flexDirection: 'column',
        boxSizing: 'border-box',
        overflow: 'hidden',
      }}
    >
      {plotType && (
        <div style={{ marginBottom: 12 }}>
          <span
            style={{
              display: 'inline-block',
              fontSize: 12,
              fontWeight: 800,
              fontFamily: 'Inter Tight, sans-serif',
              letterSpacing: '1px',
              textTransform: 'uppercase',
              padding: '4px 12px',
              borderRadius: 6,
              background: plotType === 'a-plot' ? '#EFF6FF' : '#F0FDF4',
              color: plotType === 'a-plot' ? '#1D4ED8' : '#059669',
              border: `1px solid ${plotType === 'a-plot' ? '#BFDBFE' : '#A7F3D0'}`,
            }}
          >
            {plotType === 'a-plot' ? 'A-Plot · Narrative Stakes' : 'B-Plot · Ground Truth Receipts'}
          </span>
        </div>
      )}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
        }}
      >
        <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: '-0.4px', color: '#0F172A' }}>
          {title}
        </div>
        {badge && (
          <span
            style={{
              background: badgeColor,
              color: badgeTextColor,
              fontWeight: 800,
              fontSize: 14,
              padding: '5px 14px',
              borderRadius: 999,
              letterSpacing: '0.3px',
              border: `1px solid ${borderColor}`,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      {subtitle && (
        <div style={{ fontSize: 17, color: '#64748B', fontWeight: 600, marginBottom: 18 }}>
          {subtitle}
        </div>
      )}
      {children}
    </div>
  );
};
