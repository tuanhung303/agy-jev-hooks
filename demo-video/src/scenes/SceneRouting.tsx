import React from 'react';
import { Card } from '../components/Card';
import { Subtitle } from '../components/Subtitle';

export const SceneRouting: React.FC = () => {
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
            color: '#1D4ED8',
            fontWeight: 800,
            fontSize: 16,
            letterSpacing: '1.5px',
            textTransform: 'uppercase',
            fontFamily: 'Inter Tight, sans-serif',
          }}
        >
          Act 1 · Question Before Explanation
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
          Can We Steer Agent Intent in &lt;150ms Before It Writes Bad Code?
        </h1>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1.1fr 0.9fr',
          gap: 36,
        }}
      >
        <Card
          title="User Prompt & Context"
          subtitle="Evaluated at UserPromptSubmit / PreInvocation"
          plotType="a-plot"
          delay={0}
        >
          <div
            style={{
              background: '#0F172A',
              padding: '18px 22px',
              borderRadius: 10,
              border: '1px solid #334155',
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 16,
              lineHeight: 1.6,
              color: '#F8FAFC',
            }}
          >
            <div style={{ color: '#38BDF8', fontSize: 13, marginBottom: 8, fontWeight: 700 }}>
              user@dev-box:~/agy-jev-hooks ❯
            </div>
            "rút gọn hàm parse_payload này bằng thư viện chuẩn, không thêm abstraction thừa"
          </div>
          <div style={{ marginTop: 16, fontSize: 15.5, color: '#64748B', lineHeight: 1.5 }}>
            Jev evaluates across 19 registered skills in under 150ms against project rules and AGENTS.md.
          </div>
        </Card>

        <Card
          title="Jev Choice Classifier"
          subtitle="Single Choice Question (Floor 0.60)"
          badge="Score >= 0.60"
          badgeColor="#FEF3C7"
          badgeTextColor="#B45309"
          borderColor="#F59E0B"
          plotType="b-plot"
          delay={12}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '10px 16px',
                background: '#FEF3C7',
                border: '2px solid #D97706',
                borderRadius: 8,
                fontWeight: 800,
                fontSize: 18,
                color: '#B45309',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>/ponytail (WINNER)</span>
              <span>p = 0.94</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '8px 16px',
                background: '#F8FAFC',
                border: '1px solid #E2E8F0',
                borderRadius: 8,
                fontSize: 15,
                color: '#64748B',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>/tdd</span>
              <span>p = 0.04</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '8px 16px',
                background: '#F8FAFC',
                border: '1px solid #E2E8F0',
                borderRadius: 8,
                fontSize: 15,
                color: '#64748B',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>__none__</span>
              <span>p = 0.01</span>
            </div>
          </div>
          <div
            style={{
              marginTop: 14,
              background: '#F0FDF4',
              border: '1.5px solid #059669',
              padding: '10px 16px',
              borderRadius: 8,
              color: '#065F46',
              fontSize: 14.5,
              fontWeight: 700,
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            Injected: ※ skill suggestion: /ponytail
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
        Injected in 140ms before the generative model produces a single token!
      </div>

      <Subtitle text="At prompt submission, Jev classifies user intent against 19 skills in under 150ms and injects routing advice." />
    </div>
  );
};
