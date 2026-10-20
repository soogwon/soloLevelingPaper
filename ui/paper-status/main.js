import { App } from '@modelcontextprotocol/ext-apps';
import { mountStatusApp } from './status.js';

const app = new App({ name: 'Solo Leveling Paper Status', version: '0.1.0' });
mountStatusApp(app, document);
