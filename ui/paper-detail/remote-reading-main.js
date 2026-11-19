import { App } from '@modelcontextprotocol/ext-apps';
import { mountRemoteReading } from './remote-reading.js';

mountRemoteReading(new App({name: '원격 읽기 상태', version: '0.1.0'}), document);
