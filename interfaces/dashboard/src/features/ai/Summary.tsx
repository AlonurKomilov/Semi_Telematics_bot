import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/** Backward-compat redirect: /ai/summary → /ai/chat?tab=briefing */
export default function Summary() {
  const navigate = useNavigate();
  useEffect(() => {
    navigate('/ai/chat?tab=briefing', { replace: true });
  }, [navigate]);
  return null;
}
