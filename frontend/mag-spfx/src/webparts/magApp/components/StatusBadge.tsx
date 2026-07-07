import * as React from 'react';
import styles from './MagApp.module.scss';

export interface StatusBadgeProps {
  label: string;
  tone?: 'neutral' | 'running' | 'success' | 'error';
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ label, tone = 'neutral' }) => (
  <span className={`${styles.statusBadge} ${styles[`status_${tone}`]}`}>{label}</span>
);
