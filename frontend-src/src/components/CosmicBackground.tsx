import {useEffect, useRef, useState, type RefObject} from 'react';
import {mountUniverse} from '@/lib/space/universe';
import type {MotionState, UniverseController} from '@/lib/space/config';

const preferenceKey = 'aurora.cosmos.paused';
function savedPause() {try {return localStorage.getItem(preferenceKey) === '1';} catch {return false;}}

export function useCosmicScene() {
  const root = useRef<HTMLDivElement>(null), engine = useRef<UniverseController | null>(null);
  const [state, setState] = useState<MotionState>({paused: savedPause(), reduced: false, renderer: 'static'});
  useEffect(() => {
    if (!root.current) return;
    const scene = mountUniverse(root.current, setState);engine.current = scene;
    scene.setPaused(savedPause());
    return () => {scene.destroy();engine.current = null;};
  }, []);
  const toggle = () => {
    const value = !engine.current?.paused;engine.current?.setPaused(value);
    try {localStorage.setItem(preferenceKey, value ? '1' : '0');} catch {/* Preferência opcional. */}
  };
  const label = state.renderer === 'static' ? 'Fundo estático' : state.reduced ? 'Movimento reduzido' :
    state.paused ? 'Retomar animação' : 'Pausar animação';
  return {root, controls: <button type="button" className="cosmic-motion-button" onClick={toggle}
    disabled={state.reduced || state.renderer === 'static'} aria-pressed={state.paused || state.reduced}
    title={state.reduced ? 'A preferência de movimento reduzido do dispositivo está ativa.' : 'Controlar o movimento do fundo espacial'}>
    <span aria-hidden="true">{state.paused || state.reduced ? '▷' : 'Ⅱ'}</span>{label}
  </button>};
}

export function CosmicBackground({root}: {root: RefObject<HTMLDivElement | null>}) {
  return <div ref={root} className="cosmic-background" aria-hidden="true"/>;
}
