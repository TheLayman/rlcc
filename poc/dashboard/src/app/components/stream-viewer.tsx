import { Card } from '@/app/components/ui/card';
import { Activity, ExternalLink } from 'lucide-react';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { CV_BASE } from '@/lib/runtime-config';

interface StreamViewerProps {
  vasData: any[];
  posData: any[];
}

export function StreamViewer({ vasData, posData }: StreamViewerProps) {
  const cvDebugUrl = `${CV_BASE}/stream/view`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold mb-1 text-gray-800">Live Debug Feeds</h2>
          <p className="text-sm text-gray-500">Raw CV and Nukkad events from the RLCC backend, plus the standalone CV stream inspector.</p>
        </div>
        <Button
          asChild
          variant="outline"
          className="gap-2 border-gray-200 text-blue-700 hover:bg-blue-50"
        >
          <a href={cvDebugUrl} target="_blank" rel="noreferrer">
            <ExternalLink className="h-4 w-4" />
            Open CV Debug View
          </a>
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card className="bg-white border-gray-200 flex flex-col h-[700px] shadow-sm">
          <div className="p-4 border-b border-gray-200 bg-sky-50 sticky top-0 flex justify-between items-center">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-sky-600" />
              <h3 className="font-semibold text-sky-800">CV Signal Feed</h3>
            </div>
            <Badge variant="outline" className="bg-sky-50 text-sky-600 border-sky-200">
              {vasData.length} Events
            </Badge>
          </div>
          <div className="flex-1 overflow-auto p-4 space-y-3">
            {vasData.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-400">
                <p>No CV signals yet</p>
              </div>
            ) : (
              vasData.map((event, idx) => (
                <div key={`${event.camera_id || 'cv'}-${idx}`} className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs font-mono">
                  <div className="flex justify-between items-center mb-2 text-gray-500 pb-2 border-b border-gray-200">
                    <span>{event.camera_id || `CV-${idx}`}</span>
                    <span>{event.ts || 'pending'}</span>
                  </div>
                  <pre className="overflow-x-auto text-gray-700">
                    {JSON.stringify(event, null, 2)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card className="bg-white border-gray-200 flex flex-col h-[700px] shadow-sm">
          <div className="p-4 border-b border-gray-200 bg-emerald-50 sticky top-0 flex justify-between items-center">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-emerald-600" />
              <h3 className="font-semibold text-emerald-800">POS Event Feed</h3>
            </div>
            <Badge variant="outline" className="bg-emerald-50 text-emerald-600 border-emerald-200">
              {posData.length} Events
            </Badge>
          </div>
          <div className="flex-1 overflow-auto p-4 space-y-3">
            {posData.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-400">
                <p>No Nukkad events yet</p>
              </div>
            ) : (
              posData.map((event, idx) => (
                <div key={`${event.transactionSessionId || 'pos'}-${idx}`} className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs font-mono">
                  <div className="flex justify-between items-center mb-2 text-gray-500 pb-2 border-b border-gray-200">
                    <span>{event.event || `POS-${idx}`}</span>
                    <span>{event.transactionSessionId || event.transactionNumber || 'standalone'}</span>
                  </div>
                  <pre className="overflow-x-auto text-gray-700">
                    {JSON.stringify(event, null, 2)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
