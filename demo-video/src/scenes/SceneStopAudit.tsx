import React from 'react';
import { Card } from '../components/Card';
import { Subtitle } from '../components/Subtitle';

export const SceneStopAudit: React.FC = () => {
  return (
    <div
      style={{
        flex: 1,
        backgroundColor: '#FFFFFF',
        padding: '70px 100px',
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        color: '#0F172A',
        boxSizing: 'border-box',
      }}
    >
      <div style={{ marginBottom: 28 }}>
        <span
          style={{
            color: '#DC2626',
            fontWeight: 800,
            fontSize: 16,
            letterSpacing: '1.5px',
            textTransform: 'uppercase',
            fontFamily: 'Inter Tight, sans-serif',
          }}
        >
          Act 2 · A/B Plot Clash (Route H Undone)
        </span>
        <h1
          style={{
            fontSize: 46,
            fontWeight: 900,
            letterSpacing: '-1.2px',
            marginTop: 8,
            lineHeight: 1.15,
            color: '#0F172A',
            fontFamily: 'Inter Tight, sans-serif',
          }}
        >
          Stop Hook with Jev Compass & Axis Separation
        </h1>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 36,
        }}
      >
        <Card
          title="Agent Claim vs Evidence"
          subtitle="A-Plot Agent: 'Added retry limit. All tests pass.'"
          borderColor="#E2E8F0"
          backgroundColor="#F8FAFC"
          plotType="a-plot"
          delay={0}
        >
          <div
            style={{
              background: '#0F172A',
              padding: '16px 20px',
              borderRadius: 10,
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 14.5,
              lineHeight: 1.6,
              color: '#F8FAFC',
              border: '1px solid #334155',
            }}
          >
            <div>Target: scripts/sync.py (added RETRY_LIMIT = 3)</div>
            <div style={{ color: '#F87171', marginTop: 8, fontWeight: 700 }}>
              Deficit: ZERO pytest commands in execution history!
            </div>
            <div style={{ color: '#94A3B8', marginTop: 4 }}>
              Deliverable 'run full test suite' is demonstrably absent.
            </div>
          </div>
        </Card>

        <Card
          title="Jev Compass Verdict"
          subtitle="B-Plot: Hard Escalate Gate"
          badge="Stop Blocked (Exit 2)"
          badgeColor="#FEE2E2"
          badgeTextColor="#DC2626"
          borderColor="#DC2626"
          backgroundColor="#FEF2F2"
          plotType="b-plot"
          delay={12}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '10px 16px',
                background: '#FFFFFF',
                borderRadius: 8,
                fontSize: 15,
                color: '#334155',
                border: '1px solid #CBD5E1',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>q_pass (Completeness)</span>
              <span style={{ color: '#DC2626', fontWeight: 800 }}>p = 0.15 (FAIL)</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '10px 16px',
                background: '#FEE2E2',
                border: '1.5px solid #DC2626',
                borderRadius: 8,
                fontSize: 15,
                fontWeight: 800,
                color: '#DC2626',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>label: undone</span>
              <span>p = 0.88 (FIRE &gt;= 0.75)</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '10px 16px',
                background: '#FEF3C7',
                border: '1px solid #D97706',
                borderRadius: 8,
                fontSize: 15,
                fontWeight: 700,
                color: '#B45309',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>label: code_slop (Note Only)</span>
              <span>p = 0.02 (CLEAN)</span>
            </div>
          </div>
        </Card>
      </div>

      <div
        style={{
          marginTop: 20,
          alignSelf: 'flex-end',
          background: '#FEF9C3',
          border: '1px solid #FDE047',
          padding: '10px 20px',
          borderRadius: 6,
          fontFamily: "'Shantell Sans', cursive",
          fontSize: 16,
          color: '#1E293B',
        }}
      >
        A-Plot makes a bare claim; B-Plot receipts prove pytest never ran. Stop slammed shut!
      </div>

      <Subtitle text="A-Plot Agent claims tests pass; B-Plot receipts show zero pytest calls. Jev Compass fires undone (p=0.88) and slams the gate shut!" />
    </div>
  );
};
