import { App } from '@modelcontextprotocol/ext-apps';
import { mountServerSync } from './server-sync.js';
mountServerSync(new App({name: 'Solo Leveling Server Sync', version: '0.1.0'}), document);
