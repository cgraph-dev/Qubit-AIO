import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceEditHud } from '@/components/VoiceEditHud';

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-27T12:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('VoiceEditHud (mock feed)', () => {
  it('starts gate-closed, streams the transcript and opens on preview', () => {
    render(<VoiceEditHud mock />);

    const apply = screen.getByRole('button', { name: /^Apply/ });
    expect(apply).toBeDisabled();
    expect(screen.getByText('REDUCED MOTION')).toBeInTheDocument();

    advance(1500);
    expect(
      screen.getByText('make this sound more confident'),
    ).toBeInTheDocument();
    expect(screen.getByText('PHASE 01')).toBeInTheDocument();
    expect(screen.getByText('FINAL')).toBeInTheDocument();

    advance(1200);
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Review');
    expect(screen.getByRole('button', { name: /^Apply/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Copy/ })).toBeEnabled();
    expect(screen.getAllByText('842 ms').length).toBeGreaterThan(0);
  });

  it('applies from the Ctrl+Enter shortcut and logs a receipt', () => {
    render(<VoiceEditHud mock />);
    advance(2600);

    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });

    expect(screen.getByTestId('status-pill')).toHaveTextContent('Applied');
    expect(screen.getByText('Apply edit')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Apply/ })).toBeDisabled();
  });

  it('opens the editor overlay, submits and shows the edited proposal', () => {
    render(<VoiceEditHud mock />);
    advance(2600);

    fireEvent.click(screen.getByRole('button', { name: /^Edit/ }));
    const dialog = screen.getByRole('dialog');
    const area = within(dialog).getByRole('textbox');
    expect(area).toHaveValue(
      'Could you send me the notes from the meeting? I believe they are in the shared folder.',
    );

    fireEvent.change(area, { target: { value: 'A manual proposal.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Submit edit/ }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'proposed' }));
    expect(screen.getAllByText('A manual proposal.').length).toBeGreaterThan(0);
    expect(screen.getByText('Manual edit')).toBeInTheDocument();
  });

  it('copies without leaving the preview and records the receipt', () => {
    render(<VoiceEditHud mock />);
    advance(2600);

    fireEvent.click(screen.getByRole('button', { name: /^Copy/ }));
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Review');
    expect(screen.getByText('Copy result')).toBeInTheDocument();
    expect(
      screen.getByText(/Copy request sent|Proposal copied to the clipboard/i),
    ).toBeInTheDocument();
  });

  it('shows the typed refusal banner in the demo', () => {
    render(<VoiceEditHud mock />);
    advance(2600);

    fireEvent.click(screen.getByRole('button', { name: 'REFUSAL' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('voice_edit_injection_refused');
    expect(alert).toHaveTextContent('Instruction-like text');

    fireEvent.click(screen.getByRole('button', { name: /Dismiss the refusal/ }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('replays the fixture from a clean state', () => {
    render(<VoiceEditHud mock />);
    advance(2600);
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Review');

    fireEvent.click(screen.getByRole('button', { name: 'REPLAY' }));
    advance(120);
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Listening');
    expect(screen.queryByText('Apply edit')).not.toBeInTheDocument();
  });

  it('retry ends in the flagged preview with the review notes', () => {
    render(<VoiceEditHud mock />);
    advance(2600);

    fireEvent.click(screen.getByRole('button', { name: /^Retry/ }));
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Thinking');
    advance(1100);

    expect(screen.getByTestId('status-pill')).toHaveTextContent('Review');
    expect(
      screen.getByText(/Instruction-like text in the selection was treated as data/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/2 identifiers from the original were not found/i),
    ).toBeInTheDocument();
  });
});

describe('VoiceEditHud without mock or token', () => {
  it('shows a typed no-token link state and keeps actions closed', () => {
    render(<VoiceEditHud />);
    expect(screen.getByText('NO TOKEN')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Apply/ })).toBeDisabled();
    expect(
      screen.getByText(/open this HUD from the helper link/i),
    ).toBeInTheDocument();
  });
});
