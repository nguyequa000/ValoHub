import { createShell } from './shell.js';
import library from './spaces/library.js';
import replay from './spaces/replay.js';
import analysis from './spaces/analysis.js';
import coach from './spaces/coach.js';
import providers from './spaces/providers.js';
import settings from './spaces/settings.js';

const shell = createShell({ spaces: [library, replay, analysis, coach, providers, settings] });
window.valohub = Object.freeze({ navigate: shell.navigate });
shell.start();
