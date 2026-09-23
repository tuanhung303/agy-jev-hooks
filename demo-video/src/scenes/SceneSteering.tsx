import React from 'react';
import { Card } from '../components/Card';
import { Subtitle } from '../components/Subtitle';

export const SceneSteering: React.FC = () => {
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
            color: '#059669',
            fontWeight: 800,
            fontSize: 16,
            letterSpacing: '1.5px',
            textTransform: 'uppercase',
            fontFamily: 'Inter Tight, sans-serif',
          }}
        >
          Act 3 · Self-Healing & The Clean Pass
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
          Actionable Steering & Clean Exit Pass
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
          title="Hook Actionable Steer"
          subtitle="A-Plot: Injected Remediation Command"
          borderColor="#1D4ED8"
          backgroundColor="#EFF6FF"
          plotType="a-plot"
          delay={0}
        >
          <div
            style={{
              background: '#FFFFFF',
              border: '1.5px solid #1D4ED8',
              padding: '16px 20px',
              borderRadius: 10,
              fontSize: 14.5,
              lineHeight: 1.6,
              color: '#0F172A',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            <div style={{ fontWeight: 800, marginBottom: 6, color: '#1E40AF' }}>
              [zcode-stop-audit] Injected Steer:
            </div>
            "Change to sync/retry.py reaches export peers; receipts cover only the target. Re-run checks covering affected peers."
          </div>
        </Card>

        <Card
          title="Agent Loop Correction"
          subtitle="B-Plot: Complete Re-run & Clean Pass"
          badge="Clean Pass"
          badgeColor="#DCFCE7"
          badgeTextColor="#16A34A"
          borderColor="#059669"
          backgroundColor="#F0FDF4"
          plotType="b-plot"
          delay={12}
        >
          <div
            style={{
              background: '#0F172A',
              padding: '16px 20px',
              borderRadius: 10,
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 14.5,
              lineHeight: 1.6,
              color: '#34D399',
            }}
          >
            <div>$ pytest tests/ -q</div>
            <div style={{ color: '#4ADE80', fontWeight: 700, marginTop: 4 }}>
              ✓ 220 passed in 6.10s (PEERS FULLY VERIFIED)
            </div>
            <div style={{ color: '#E2E8F0', marginTop: 10, fontSize: 13.5 }}>
              Re-Evaluation: q_pass = 0.98 · PASS APPROVED!
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
        Veritasium resolution: The loop closes not with blind faith, but with reproducible receipts.
      </div>

      <Subtitle text="Actionable steering forces the agent to run full peer tests (220/220 passed). Re-audit confirms q_pass=0.98 for an approved clean exit." />
    </div>
  );
};
