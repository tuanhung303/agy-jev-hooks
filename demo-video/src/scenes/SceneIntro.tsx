import React from 'react';
import { Card } from '../components/Card';
import { Subtitle } from '../components/Subtitle';

export const SceneIntro: React.FC = () => {
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
      <div style={{ marginBottom: 30 }}>
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
          Act 1 · The Misconception
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
          Why Big LLMs Fail at Agent Verification (And the 142ms Fix)
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
          title="Generative LLM (System 2)"
          subtitle="Claude / GPT-4 / Gemini Deliberation"
          badge="Heavy Reasoning"
          badgeColor="#FEE2E2"
          badgeTextColor="#DC2626"
          borderColor="#E2E8F0"
          backgroundColor="#F8FAFC"
          plotType="a-plot"
          delay={0}
        >
          <ul style={{ fontSize: 18, lineHeight: 1.7, color: '#334155', paddingLeft: 20 }}>
            <li>Autoregressive token stream prone to prompt drift & excuses</li>
            <li>High latency (2.0s - 15.0s per turn) traps agents in delays</li>
            <li>Expensive token-based billing on every intermediate step</li>
            <li>Too slow for inline pre-tool checks and stop execution barriers</li>
          </ul>
        </Card>

        <Card
          title="TypeSafe Jev (System 1)"
          subtitle="State + Questions Decision Engine"
          badge="⚡ 142ms Response"
          badgeColor="#DCFCE7"
          badgeTextColor="#16A34A"
          borderColor="#059669"
          backgroundColor="#F0FDF4"
          plotType="b-plot"
          delay={12}
        >
          <ul style={{ fontSize: 18, lineHeight: 1.7, color: '#1E293B', paddingLeft: 20 }}>
            <li>3 question types only: Boolean, Choice, Score</li>
            <li>Zero-hallucination: returns calibrated probabilities [0.0..1.0]</li>
            <li>Fractions of LLM cost; strictly bounded by 8.0s timeout</li>
            <li>Deterministic routing: built specifically for CLI pre/post hooks</li>
          </ul>
        </Card>
      </div>

      <div
        style={{
          marginTop: 24,
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
        Misconception: LLMs cannot judge LLMs in real time. We need a calibrated reflex!
      </div>

      <Subtitle text="TypeSafe Jev operates as an instant System 1 reflex: evaluating state and questions without slow generative LLM lag." />
    </div>
  );
};
