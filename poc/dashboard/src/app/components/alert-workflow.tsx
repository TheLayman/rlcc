import { useMemo, useState } from 'react';
import { format } from 'date-fns';
import { AlertTriangle, CheckCircle, Clock, Search as SearchIcon, Video } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, ClipStatus, CVConfidence, Transaction } from '@/lib/mock-data';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Card } from '@/app/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/app/components/ui/select';
import { Textarea } from '@/app/components/ui/textarea';
import { BACKEND_BASE } from '@/lib/runtime-config';

interface AlertWorkflowProps {
  alerts: Alert[];
  setAlerts: React.Dispatch<React.SetStateAction<Alert[]>>;
  transactions: Transaction[];
  onOpenTransaction?: (alert: Alert) => void;
}

function clipStatusLabel(status: ClipStatus): string {
  switch (status) {
    case 'pending': return 'Clip pending';
    case 'outside_buffer': return 'Outside buffer';
    case 'camera_unmapped': return 'Camera unmapped';
    case 'retention_expired': return 'Retention expired';
    case 'not_recorded': return 'Not recorded';
    default: return 'Clip unavailable';
  }
}

function cvConfidenceBadgeClass(level: CVConfidence): string {
  switch (level) {
    case 'VERY_HIGH':
      return 'bg-red-100 text-red-800 border-red-300';
    case 'HIGH':
      return 'bg-red-50 text-red-700 border-red-200';
    case 'MEDIUM':
      return 'bg-amber-50 text-amber-700 border-amber-200';
    default:
      return 'bg-gray-50 text-gray-600 border-gray-200';
  }
}

function humanizeRule(rule: string): string {
  const [prefix, ...rest] = rule.split('_');
  const name = rest.length ? rest.join(' ') : prefix;
  return `${prefix}. ${name.replace(/\b\w/g, (c) => c.toUpperCase())}`;
}

export function AlertWorkflow({ alerts, setAlerts, transactions, onOpenTransaction }: AlertWorkflowProps) {
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [remarks, setRemarks] = useState('');
  const [newStatus, setNewStatus] = useState('');

  const ruleTypeOptions = useMemo(() => {
    const seen = new Map<string, number>();
    alerts.forEach(alert => {
      (alert.triggered_rules || []).forEach(rule => {
        seen.set(rule, (seen.get(rule) || 0) + 1);
      });
    });
    return Array.from(seen.entries())
      .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
      .map(([rule, count]) => ({ value: rule, label: `${humanizeRule(rule)} (${count})` }));
  }, [alerts]);

  const filteredAlerts = useMemo(() => {
    let result = alerts;
    if (statusFilter === 'open') result = result.filter(a => ['new', 'Fraudulent', 'Pending for review'].includes(a.status));
    else if (statusFilter === 'investigating') result = result.filter(a => a.status === 'reviewing');
    else if (statusFilter === 'closed') result = result.filter(a => ['resolved', 'Genuine'].includes(a.status));

    if (typeFilter !== 'all') {
      result = result.filter(a => (a.triggered_rules || []).includes(typeFilter));
    }

    return result;
  }, [alerts, statusFilter, typeFilter]);

  const summary = useMemo(() => {
    const open = alerts.filter(a => ['new', 'Fraudulent', 'Pending for review'].includes(a.status)).length;
    const investigating = alerts.filter(a => a.status === 'reviewing').length;
    const closed = alerts.filter(a => ['resolved', 'Genuine'].includes(a.status)).length;
    return { total: alerts.length, open, investigating, closed };
  }, [alerts]);

  const handleResolve = async (alertId: string, status: string, remarksText: string) => {
    if (!status) {
      toast.error('Please select a resolution status');
      return;
    }
    if (!remarksText.trim()) {
      toast.error('Please add remarks before resolving');
      return;
    }

    setAlerts(prev => prev.map(alert => (
      alert.id === alertId ? { ...alert, status, remarks: remarksText } : alert
    )));

    const alert = alerts.find(item => item.id === alertId);
    if (alert?.transaction_id && alert.transaction_id !== 'N/A') {
      fetch(
        `${BACKEND_BASE}/api/admin/validate?transaction_id=${alert.transaction_id}&decision=${encodeURIComponent(status)}&notes=${encodeURIComponent(remarksText)}`,
        { method: 'POST' },
      ).catch(() => {});
    } else {
      fetch(
        `${BACKEND_BASE}/api/alerts/${alertId}/resolve?status=${encodeURIComponent(status)}&remarks=${encodeURIComponent(remarksText)}`,
        { method: 'POST' },
      ).catch(() => {});
    }

    toast.success(`Alert ${alertId} updated`);
    setSelectedAlertId(null);
    setRemarks('');
    setNewStatus('');
  };

  const getStatusIcon = (status: string) => {
    if (['new', 'Fraudulent', 'Pending for review'].includes(status)) {
      return <AlertTriangle className="h-4 w-4 text-red-500" />;
    }
    if (status === 'reviewing') {
      return <Clock className="h-4 w-4 text-amber-500" />;
    }
    return <CheckCircle className="h-4 w-4 text-green-500" />;
  };

  const getStatusBadge = (status: string) => {
    if (['new', 'Fraudulent'].includes(status)) {
      return <Badge className="bg-red-50 text-red-700 border-red-200">{status}</Badge>;
    }
    if (['Pending for review', 'reviewing'].includes(status)) {
      return <Badge className="bg-amber-50 text-amber-700 border-amber-200">{status}</Badge>;
    }
    return <Badge className="bg-green-50 text-green-700 border-green-200">{status}</Badge>;
  };

  const getTransaction = (txnId: string) => transactions.find(txn => txn.id === txnId);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-white border-gray-200 p-4 shadow-sm cursor-pointer hover:bg-gray-50" onClick={() => setStatusFilter('all')}>
          <p className="text-sm text-gray-500">Total Alerts</p>
          <p className="text-3xl font-bold text-gray-800">{summary.total}</p>
        </Card>
        <Card className="bg-red-50 border-red-200 p-4 shadow-sm cursor-pointer hover:bg-red-100" onClick={() => setStatusFilter('open')}>
          <p className="text-sm text-red-600">Open / Active</p>
          <p className="text-3xl font-bold text-red-700">{summary.open}</p>
        </Card>
        <Card className="bg-amber-50 border-amber-200 p-4 shadow-sm cursor-pointer hover:bg-amber-100" onClick={() => setStatusFilter('investigating')}>
          <p className="text-sm text-amber-600">Under Investigation</p>
          <p className="text-3xl font-bold text-amber-700">{summary.investigating}</p>
        </Card>
        <Card className="bg-green-50 border-green-200 p-4 shadow-sm cursor-pointer hover:bg-green-100" onClick={() => setStatusFilter('closed')}>
          <p className="text-sm text-green-600">Closed / Resolved</p>
          <p className="text-3xl font-bold text-green-700">{summary.closed}</p>
        </Card>
      </div>

      <Card className="bg-white border-gray-200 shadow-sm">
        <div className="p-4 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-lg font-semibold text-gray-800">Alert Management</h3>
          <div className="flex items-center gap-2">
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-[240px] bg-white border-gray-200 h-9 text-xs">
                <SelectValue placeholder="Alert type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All alert types</SelectItem>
                {ruleTypeOptions.map(option => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Badge variant="outline" className="text-gray-500">
              {filteredAlerts.length} {statusFilter === 'all' && typeFilter === 'all' ? 'total' : 'filtered'}
            </Badge>
          </div>
        </div>

        <div className="divide-y divide-gray-100 max-h-[600px] overflow-y-auto">
          {filteredAlerts.length === 0 ? (
            <div className="text-center py-12 text-gray-400">
              <SearchIcon className="h-12 w-12 mx-auto mb-2 opacity-30" />
              <p>No alerts in this category</p>
            </div>
          ) : (
            filteredAlerts.map(alert => {
              const txn = getTransaction(alert.transaction_id);
              const isSelected = selectedAlertId === alert.id;
              const clipUrl = alert.clip_url ? `${BACKEND_BASE}${alert.clip_url}` : txn?.clip_url ? `${BACKEND_BASE}${txn.clip_url}` : null;
              const hasClip = Boolean(clipUrl);

              return (
                <div key={alert.id} className={`p-4 ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3 flex-1">
                      {getStatusIcon(alert.status)}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-mono text-sm font-medium text-gray-800">
                            {alert.transaction_id !== 'N/A' ? alert.transaction_id : (alert.triggered_rules?.[0] || alert.id)}
                          </span>
                          {getStatusBadge(alert.status)}
                          <Badge className={`${alert.risk_level === 'High' ? 'bg-red-50 text-red-700 border-red-200' : alert.risk_level === 'Medium' ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-green-50 text-green-700 border-green-200'}`}>
                            {alert.risk_level}
                          </Badge>
                          {alert.cv_confidence && alert.cv_confidence !== '' && (
                            <Badge
                              className={cvConfidenceBadgeClass(alert.cv_confidence as CVConfidence)}
                              title="CV confidence ladder (Phase 2)"
                            >
                              CV {alert.cv_confidence.replace('_', ' ')}
                            </Badge>
                          )}
                        </div>
                        <div className="text-sm text-gray-600">
                          Store: {alert.shop_name || alert.shop_id} | Cashier: {alert.cashier_name}
                          {txn && ` | ₹${txn.transaction_total.toLocaleString('en-IN')}`}
                        </div>
                        <div className="text-xs text-gray-400 mt-1">
                          {format(alert.timestamp, 'MMM dd, yyyy HH:mm:ss')}
                          {alert.triggered_rules && alert.triggered_rules.length > 0 && (
                            <span className="ml-2 text-red-500">{alert.triggered_rules.join(', ')}</span>
                          )}
                        </div>
                        {alert.remarks && (
                          <div className="mt-2 text-xs text-gray-500 bg-gray-50 rounded-md px-3 py-2">
                            {alert.remarks}
                          </div>
                        )}
                        {!hasClip && alert.clip_reason && (
                          <div className="mt-2 rounded-md border border-amber-100 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
                            Why no clip: {alert.clip_reason}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => onOpenTransaction?.(alert)}
                          className={`gap-1 border-gray-200 text-xs ${hasClip ? 'text-blue-600 hover:bg-blue-50' : 'text-gray-500 hover:bg-gray-50'}`}
                          title={hasClip ? 'Play alert footage' : alert.clip_reason || 'Open alert — clip will load when available'}
                        >
                          <Video className="h-3 w-3" />
                          {hasClip ? 'Play Video' : 'View Footage'}
                        </Button>
                        {['new', 'Fraudulent', 'Pending for review', 'reviewing'].includes(alert.status) && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="border-blue-200 text-blue-600 hover:bg-blue-50"
                            onClick={() => setSelectedAlertId(isSelected ? null : alert.id)}
                          >
                            {isSelected ? 'Cancel' : 'Resolve'}
                          </Button>
                        )}
                      </div>
                      {!hasClip && (
                        <span className="text-[10px] uppercase tracking-wide text-amber-700">
                          {clipStatusLabel(alert.clip_status || 'unknown')}
                        </span>
                      )}
                    </div>
                  </div>

                  {isSelected && (
                    <div className="mt-4 ml-7 p-4 bg-white rounded-lg border border-gray-200 space-y-3">
                      <div>
                        <label className="text-sm font-medium text-gray-600 mb-1 block">Resolution Status *</label>
                        <Select value={newStatus} onValueChange={setNewStatus}>
                          <SelectTrigger className="bg-gray-50 border-gray-200">
                            <SelectValue placeholder="Select resolution..." />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="resolved">Closed - Resolved</SelectItem>
                            <SelectItem value="Genuine">Closed - Genuine</SelectItem>
                            <SelectItem value="reviewing">Under Investigation</SelectItem>
                            <SelectItem value="Fraudulent">Confirmed Fraudulent</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div>
                        <label className="text-sm font-medium text-gray-600 mb-1 block">Remarks *</label>
                        <Textarea
                          value={remarks}
                          onChange={event => setRemarks(event.target.value)}
                          placeholder="Add investigation notes or resolution remarks..."
                          className="bg-gray-50 border-gray-200 min-h-[80px]"
                        />
                      </div>
                      <div className="flex gap-2">
                        <Button
                          className="bg-blue-600 hover:bg-blue-700 text-white"
                          onClick={() => handleResolve(alert.id, newStatus, remarks)}
                        >
                          Submit Resolution
                        </Button>
                        <Button variant="outline" onClick={() => { setSelectedAlertId(null); setRemarks(''); setNewStatus(''); }}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}
