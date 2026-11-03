import { App } from '@modelcontextprotocol/ext-apps';
import { mountQuiz } from './quiz.js';

mountQuiz(new App({ name: 'Solo Leveling Quiz', version: '0.1.0' }), document);
