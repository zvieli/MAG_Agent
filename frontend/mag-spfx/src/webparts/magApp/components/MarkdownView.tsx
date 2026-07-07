import { marked } from 'marked';
import * as React from 'react';
import styles from './MagApp.module.scss';

export interface MarkdownViewProps {
  markdown: string;
  variant?: 'block' | 'inline';
}

const stripUnsafeHtml = (html: string): string =>
  html
    .replace(/<script[\s\S]*?>[\s\S]*?<\/script>/gi, '')
    .replace(/<(iframe|object|embed|style|link|meta)[\s\S]*?>[\s\S]*?<\/\1>/gi, '')
    .replace(/<(iframe|object|embed|style|link|meta)[^>]*?>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/\son\w+='[^']*'/gi, '')
    .replace(/\s(href|src)="\s*(javascript|data:text\/html):[^"]*"/gi, '')
    .replace(/\s(href|src)='\s*(javascript|data:text\/html):[^']*'/gi, '');

export const MarkdownView: React.FC<MarkdownViewProps> = ({ markdown, variant = 'block' }) => {
  const html = React.useMemo(
    () => stripUnsafeHtml((variant === 'inline' ? marked.parseInline(markdown) : marked.parse(markdown)) as string),
    [markdown, variant]
  );

  if (variant === 'inline') {
    return <span className={styles.markdownInline} dangerouslySetInnerHTML={{ __html: html }} />;
  }

  return <article className={styles.markdown} dangerouslySetInnerHTML={{ __html: html }} />;
};
