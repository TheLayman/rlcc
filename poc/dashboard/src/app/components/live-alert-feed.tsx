import { useEffect, useState } from 'react';
import { format } from 'date-fns';
import { Card } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Bell, AlertTriangle, Eye, X } from 'lucide-react';
import { Alert } from '@/lib/mock-data';

interface LiveAlertFeedProps {
  alerts: Alert[];
  onViewAlert: (transactionId: string) => void;
  onDismissAlert: (alertId: string) => void;
}

export function LiveAlertFeed({ alerts, onViewAlert, onDismissAlert }: LiveAlertFeedProps) {
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      setPulse((prev) => !prev);
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const activeAlerts = alerts.filter((alert) =>
    alert.status === 'new' ||
    alert.status === 'Fraudulent' ||
    alert.status === 'Pending for review'
  );

  return (
    <Card className="bg-white border-gray-200 flex-1 min-h-0 flex flex-col shadow-sm">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Bell className="h-5 w-5 text-red-500" />
            {activeAlerts.length > 0 && (
              <span
                className={`absolute -top-1 -right-1 h-3 w-3 bg-red-500 rounded-full transition-opacity duration-500 ${pulse ? 'opacity-100' : 'opacity-50'}`}
              />
            )}
          </div>
          <h3 className="font-semibold text-gray-800">Real-time Alerts</h3>
          {activeAlerts.length > 0 && (
            <Badge className="bg-red-50 text-red-600 border-red-200">
              {activeAlerts.length} New
            </Badge>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="p-4 space-y-3">
          {alerts.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <Bell className="h-12 w-12 mx-auto mb-2 opacity-30" />
              <p>No active alerts</p>
            </div>
          ) : (
            alerts.map((alert) => (
              <Card
                key={alert.id}
                className={`p-3 border ${(alert.status === 'new' || alert.status === 'Fraudulent')
                  ? 'bg-red-50 border-red-200'
                  : (alert.status === 'reviewing' || alert.status === 'Pending for review')
                    ? 'bg-amber-50 border-amber-200'
                    : 'bg-green-50 border-green-200'
                  }`}
              >
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <AlertTriangle
                      className={`h-4 w-4 ${(alert.status === 'new' || alert.status === 'Fraudulent')
                        ? 'text-red-500'
                        : (alert.status === 'reviewing' || alert.status === 'Pending for review')
                          ? 'text-amber-500'
                          : 'text-green-500'
                        }`}
                    />
                    <div className="flex flex-col">
                      <span className="text-xs font-mono text-gray-500">
                        {alert.transaction_id}
                      </span>
                      <span className={`text-[10px] uppercase font-bold ${(alert.status === 'Fraudulent') ? 'text-red-600' :
                        (alert.status === 'Pending for review') ? 'text-amber-600' :
                          'text-gray-500'
                        }`}>
                        {alert.status}
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-gray-400 hover:text-gray-600"
                    onClick={() => onDismissAlert(alert.id)}
                  >
                    <X className="h-3 w-3" />
                  </Button>
                </div>

                <div className="space-y-1 mb-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-700">Shop: {alert.shop_id}</span>
                    <Badge
                      className={`${alert.risk_level === 'High'
                        ? 'bg-red-50 text-red-700 border-red-200'
                        : alert.risk_level === 'Medium'
                          ? 'bg-amber-50 text-amber-700 border-amber-200'
                          : 'bg-green-50 text-green-700 border-green-200'
                        }`}
                    >
                      {alert.risk_level}
                    </Badge>
                  </div>
                  <div className="text-sm text-gray-500">
                    Cashier: {alert.cashier_name}
                  </div>
                  <div className="text-xs text-gray-400">
                    {format(alert.timestamp, 'MMM dd, yyyy HH:mm:ss')}
                  </div>
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  className="w-full gap-2 border-blue-200 text-blue-600 hover:bg-blue-50"
                  onClick={() => onViewAlert(alert.transaction_id)}
                >
                  <Eye className="h-4 w-4" />
                  Review Transaction
                </Button>
              </Card>
            ))
          )}
        </div>
      </div>
    </Card>
  );
}
