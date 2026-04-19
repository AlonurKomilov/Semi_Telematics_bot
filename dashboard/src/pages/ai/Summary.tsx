import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import type { AISummaryResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';

export default function Summary() {
  const [summary, setSummary] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  async function generate() {
    setLoading(true);
    setError('');
    try {
      const data = await apiJSON<AISummaryResponse>('/ai/summary', { method: 'POST' });
      setSummary(data.summary);
      setSuggestions(data.suggestions || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate summary');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">📊 Fleet Briefing</h1>
        <button
          onClick={generate}
          disabled={loading}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm transition-colors disabled:opacity-50"
        >
          {loading ? 'Generating...' : summary ? 'Refresh' : 'Generate Briefing'}
        </button>
      </div>

      {error && <p className="text-red-400 mb-4">{error}</p>}

      {!summary && !loading && !error && (
        <div className="text-center text-gray-500 mt-20">
          <p className="text-4xl mb-3">📊</p>
          <p className="text-lg font-medium">AI Fleet Briefing</p>
          <p className="text-sm mt-1">Generate an executive summary of your fleet status, health, events, and recommendations.</p>
          <button
            onClick={generate}
            className="mt-6 px-6 py-3 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors"
          >
            Generate Briefing
          </button>
        </div>
      )}

      {loading && (
        <div className="flex justify-center mt-16">
          <div className="text-gray-400 text-sm">
            <span className="inline-flex gap-1 text-lg mr-2">
              <span className="animate-bounce" style={{ animationDelay: '0ms' }}>●</span>
              <span className="animate-bounce" style={{ animationDelay: '150ms' }}>●</span>
              <span className="animate-bounce" style={{ animationDelay: '300ms' }}>●</span>
            </span>
            Analyzing your fleet...
          </div>
        </div>
      )}

      {summary && !loading && (
        <div
          className="bg-gray-800 rounded-xl p-6 text-sm text-gray-200 leading-relaxed ai-response"
          dangerouslySetInnerHTML={{ __html: formatAIResponse(summary) }}
        />
      )}

      {suggestions.length > 0 && !loading && (
        <div className="mt-4 flex flex-wrap gap-2">
          <span className="text-xs text-gray-500 self-center mr-1">Follow-up:</span>
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => navigate('/ai/chat', { state: { initialMessage: s } })}
              className="px-3 py-1.5 text-xs rounded-full bg-gray-800 text-gray-300 border border-gray-700 hover:bg-gray-700 hover:border-gray-600 transition-colors cursor-pointer"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
