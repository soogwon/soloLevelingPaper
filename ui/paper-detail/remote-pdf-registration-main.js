import { App } from '@modelcontextprotocol/ext-apps';
import { mountRemotePdfRegistration } from './remote-pdf-registration.js';

mountRemotePdfRegistration(new App({name: '원격 PDF 등록', version: '1.0.0'}), document);
