import * as Primitive from '@radix-ui/react-dialog';
import { useRef, type ReactNode } from 'react';
import {X} from 'lucide-react';

export default function Dialog({ title, children, onClose, className = '', showClose = true }: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  className?: string;
  showClose?: boolean;
}) {
  const opener = useRef(document.activeElement as HTMLElement | null);
  return <Primitive.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Primitive.Portal>
      <Primitive.Overlay className="dialog-overlay fixed inset-0 z-50 bg-zinc-950/25 backdrop-blur-[2px]" />
      <Primitive.Content aria-describedby={undefined}
        onPointerDownOutside={event => event.preventDefault()}
        onCloseAutoFocus={event => { event.preventDefault(); if (opener.current?.isConnected) opener.current.focus(); }}
        className={`dialog-content fixed left-1/2 top-1/2 z-50 flex max-h-[92vh] w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-white outline-none ${className}`}>
        <Primitive.Title className="sr-only">{title}</Primitive.Title>
        {showClose && <button type="button" aria-label="关闭" onClick={onClose} className="ui-icon-button absolute right-3 top-3 z-10"><X size={18}/></button>}
        {children}
      </Primitive.Content>
    </Primitive.Portal>
  </Primitive.Root>;
}
