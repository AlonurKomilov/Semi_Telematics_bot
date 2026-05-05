// Bottom sheet overlay — mobile-first replacement for Telegram-UI Modal.
// Slides up from the bottom edge, dismissible by tap outside or swipe down.

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useBackButton, haptics } from '../hooks/useTelegram';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Sheet header — accepts a string or any ReactNode (e.g. icon + text). */
  title?: ReactNode;
  children: ReactNode;
  /** When true the sheet sticks to the bottom and has rounded top corners; else it takes full height. */
  height?: 'auto' | 'full';
}

export function BottomSheet({ open, onClose, title, children, height = 'auto' }: Props) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragStart = useRef<number | null>(null);
  const [translateY, setTranslateY] = useState(0);

  useBackButton(open, onClose);

  useEffect(() => {
    if (!open) {
      setTranslateY(0);
      return;
    }
    haptics.selection();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  function onTouchStart(e: React.TouchEvent) {
    dragStart.current = e.touches[0].clientY;
  }
  function onTouchMove(e: React.TouchEvent) {
    if (dragStart.current === null) return;
    const dy = e.touches[0].clientY - dragStart.current;
    if (dy > 0) setTranslateY(dy);
  }
  function onTouchEnd() {
    if (translateY > 100) {
      onClose();
    }
    dragStart.current = null;
    setTranslateY(0);
  }

  if (!open) return null;
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        ref={sheetRef}
        className={`sheet sheet--${height}`}
        style={{ transform: `translateY(${translateY}px)`, transition: translateY > 0 ? 'none' : 'transform 0.25s ease' }}
        onClick={e => e.stopPropagation()}
      >
        <div
          className="sheet__handle-area"
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
        >
          <div className="sheet__handle" />
        </div>
        {title && <div className="sheet__title">{title}</div>}
        <div className="sheet__body">{children}</div>
      </div>
    </div>
  );
}
