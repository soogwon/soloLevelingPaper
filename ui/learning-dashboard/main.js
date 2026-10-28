import { App } from '@modelcontextprotocol/ext-apps';
import { mountDashboard } from './dashboard.js';

const app = new App({ name: 'Solo Leveling Learning Dashboard', version: '0.1.0' });
mountDashboard(app, document);
