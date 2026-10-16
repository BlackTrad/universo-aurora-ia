import {mountCosmos} from '../cosmos';
import {mountWebGLUniverse} from './webgl';
import type {MotionState, UniverseController} from './config';

// Se a GPU ou um shader falhar, o painel mantém o motor Canvas já validado.
export function mountUniverse(root: HTMLElement, onMotionChange: (state: MotionState) => void): UniverseController {
  let current: UniverseController | null = null, paused = false, destroyed = false;
  function fallback() {
    if (destroyed) return;
    current?.destroy();root.dataset.renderer = 'canvas';
    current = mountCosmos(root, {constellations: false, onMotionChange: (s: {paused: boolean; reduced: boolean}) => {
      onMotionChange({...s, renderer: root.childElementCount ? 'canvas' : 'static'});
    }});
    current!.setPaused(paused);
    if (!root.childElementCount) root.dataset.renderer = 'static';
    onMotionChange({paused: current!.paused, reduced: current!.reduced, renderer: root.childElementCount ? 'canvas' : 'static'});
  }
  try {
    root.dataset.renderer = 'webgl';
    current = mountWebGLUniverse(root, {onMotionChange, onFailure: fallback});
  } catch {fallback();}
  return {
    get reduced() {return current?.reduced ?? true;},get paused() {return paused;},
    setPaused(value) {paused = Boolean(value);current?.setPaused(paused);},
    destroy() {destroyed = true;current?.destroy();current = null;},
  };
}
