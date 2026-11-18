import { App } from '@modelcontextprotocol/ext-apps';
import { mountRemoteJobCancel } from './remote-job-cancel.js';
mountRemoteJobCancel(new App({name:'원격 처리 작업 취소',version:'0.1.0'}),document);
