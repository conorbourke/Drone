/** Fallback for unknown routes. */
import { Link } from 'react-router';
import { EmptyState } from '../components/EmptyState';

export function NotFoundPage() {
  return (
    <div className="login-page">
      <EmptyState
        className="login-card"
        title="Page not found"
        description="There is nothing at this address."
        action={
          <Link to="/" className="button button-primary">
            Go to projects
          </Link>
        }
      />
    </div>
  );
}
