import React from 'react';
import { Composition, Series } from 'remotion';
import { SceneIntro } from './scenes/SceneIntro';
import { SceneRouting } from './scenes/SceneRouting';
import { SceneStopAudit } from './scenes/SceneStopAudit';
import { SceneSteering } from './scenes/SceneSteering';

const JevVideo120s: React.FC = () => {
  return (
    <Series>
      <Series.Sequence durationInFrames={480}>
        <SceneIntro />
      </Series.Sequence>
      <Series.Sequence durationInFrames={600}>
        <SceneRouting />
      </Series.Sequence>
      <Series.Sequence durationInFrames={1020}>
        <SceneStopAudit />
      </Series.Sequence>
      <Series.Sequence durationInFrames={1500}>
        <SceneSteering />
      </Series.Sequence>
    </Series>
  );
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="JevHooksExplainer120s"
      component={JevVideo120s}
      durationInFrames={3600} // 120 seconds at 30 fps
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
