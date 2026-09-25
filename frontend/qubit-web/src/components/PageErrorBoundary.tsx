import { Component, type ErrorInfo, type ReactNode } from "react";

interface PageErrorBoundaryProps {
  children: ReactNode;
  /** Human-readable page name used in the contained fallback. */
  label?: string;
}

interface PageErrorBoundaryState {
  error: Error | null;
}

/**
 * Contains a render-time crash to the page that threw.
 *
 * Without a boundary, an uncaught render error (e.g. the cron blueprints
 * ``fields is not iterable`` TypeError) propagates to the React root and blanks
 * the entire SPA. This keeps the surrounding shell — header, sidebar, nav —
 * usable and renders an honest, recoverable fallback in the page body.
 */
export class PageErrorBoundary extends Component<
  PageErrorBoundaryProps,
  PageErrorBoundaryState
> {
  state: PageErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): PageErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(
      `[page] ${this.props.label ?? "page"} render error contained`,
      error,
      info,
    );
  }

  private reset = () => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      return (
        <div
          role="alert"
          data-testid="page-error-boundary"
          className="m-4 border border-destructive/50 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            {this.props.label ?? "This page"} failed to render.
          </p>
          <p className="mt-1 text-xs text-muted-foreground break-words">
            {error.message}
          </p>
          <button
            type="button"
            className="mt-3 text-xs text-primary underline"
            onClick={this.reset}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default PageErrorBoundary;
