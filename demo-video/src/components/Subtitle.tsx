import React from 'react';

export const Subtitle: React.FC<{ text: string }> = ({ text }) => {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 50,
        left: 0,
        right: 0,
        display: 'flex',
        justifyContent: 'center',
        padding: '0 100px',
        zIndex: 50,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          background: 'rgba(15, 23, 42, 0.92)',
          color: '#FFFFFF',
          fontSize: 22,
          fontWeight: 600,
          fontFamily: 'Inter, sans-serif',
          padding: '14px 32px',
          borderRadius: 999,
          border: '1px solid rgba(255, 255, 255, 0.14)',
          textAlign: 'center',
          maxWidth: 1500,
          lineHeight: 1.35,
          boxShadow: '0 12px 30px rgba(0, 0, 0, 0.35)',
          backdropFilter: 'blur(16px)',
        }}
      >
        {text}
      </div>
    </div>
  );
};
