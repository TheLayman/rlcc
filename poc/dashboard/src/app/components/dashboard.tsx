import { useEffect, useMemo, useState } from 'react';
import { startOfDay, subDays } from 'date-fns';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Download,
  Filter,
  LayoutDashboard,
  Map as MapIcon,
  RefreshCw,
  Search,
  Settings,
  Shield,
  ShieldAlert,
  Store,
  Users,
  Video,
} from 'lucide-react';
import { toast } from 'sonner';

import { Alert, Transaction } from '@/lib/mock-data';
import { AlertWorkflow } from '@/app/components/alert-workflow';
import { AnalyticsView } from '@/app/components/analytics-view';
import { EmployeeScorecardView } from '@/app/components/employee-scorecard-view';
import { HeatmapView } from '@/app/components/heatmap-view';
import { SettingsPanel } from '@/app/components/settings-panel';
import { StoreConfigView } from '@/app/components/store-config-view';
import { StreamViewer } from '@/app/components/stream-viewer';
import { TransactionDetailDrawer } from '@/app/components/transaction-detail-drawer';
import { TransactionTable } from '@/app/components/transaction-table';
import { VideoReviewView } from '@/app/components/video-review-view';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Card } from '@/app/components/ui/card';
import { Input } from '@/app/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/app/components/ui/select';
import { BACKEND_BASE, BACKEND_WS_BASE } from '@/lib/runtime-config';

function AnimatedCount({ value }: { value: number }) {
  const [displayed, setDisplayed] = useState(0);
  useEffect(() => {
    if (displayed === value) return;
    const diff = value - displayed;
    const step = Math.max(1, Math.abs(Math.ceil(diff / 15)));
    const timer = setTimeout(() => {
      setDisplayed(prev => (diff > 0 ? Math.min(prev + step, value) : Math.max(prev - step, value)));
    }, 30);
    return () => clearTimeout(timer);
  }, [value, displayed]);
  return <>{displayed}</>;
}

function exportToCSV(transactions: Transaction[]) {
  const headers = ['Transaction ID', 'Shop ID', 'Store Name', 'POS ID', 'Cashier Name', 'Timestamp', 'Total (INR)', 'Risk Level', 'Status', 'Triggered Rules'];
  const rows = transactions.map(txn => [
    txn.id,
    txn.shop_id,
    txn.shop_name || '',
    txn.pos_id,
    txn.cashier_name,
    txn.timestamp.toISOString(),
    txn.transaction_total.toFixed(2),
    txn.risk_level,
    txn.status || 'pending',
    (txn.triggered_rules || []).join('; '),
  ]);
  const csv = [headers, ...rows]
    .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `rlcc_transactions_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

const NAV_ITEMS = [
  { id: 'transactions', label: 'Transactions', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'alerts', label: 'Alerts', icon: Bell },
  { id: 'videos', label: 'Videos', icon: Video },
  { id: 'scorecard', label: 'Store Scorecard', icon: Users },
  { id: 'heatmap', label: 'Store Overview', icon: MapIcon },
  { id: 'streams', label: 'Stream Viewer', icon: Activity },
  { id: 'store-config', label: 'Store Config', icon: Store },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

const RULE_FILTERS = [
  { value: 'all', label: 'All Violations' },
  { value: 'manual_entry', label: 'Manual Entry' },
  { value: 'manual_discount', label: 'Manual Discount' },
  { value: 'manual_price', label: 'Manual Price' },
  { value: 'void', label: 'Void / Cancel' },
  { value: 'return', label: 'Return / Refund' },
  { value: 'reprint', label: 'Reprint' },
  { value: 'drawer', label: 'Drawer Opened' },
  { value: 'employee', label: 'Employee Purchase' },
  { value: 'missing_pos', label: 'Missing POS' },
];

function matchesViolation(transaction: Transaction, violationFilter: string) {
  if (violationFilter === 'all') return true;
  const rules = (transaction.triggered_rules || []).join(' ').toLowerCase();
  switch (violationFilter) {
    case 'manual_entry':
      return rules.includes('manual_entry');
    case 'manual_discount':
      return rules.includes('manual_discount');
    case 'manual_price':
      return rules.includes('manual_price');
    case 'void':
      return rules.includes('void') || rules.includes('cancel');
    case 'return':
      return rules.includes('return') || rules.includes('refund');
    case 'reprint':
      return rules.includes('reprint');
    case 'drawer':
      return rules.includes('drawer');
    case 'employee':
      return rules.includes('employee');
    case 'missing_pos':
      return rules.includes('missing_pos');
    default:
      return true;
  }
}

export function Dashboard() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [billsMap, setBillsMap] = useState<Record<string, any>>({});
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('transactions');
  const [activeFilter, setActiveFilter] = useState<'all' | 'high' | 'medium' | 'pending'>('all');
  const [timeRange, setTimeRange] = useState('all');
  const [storeFilter, setStoreFilter] = useState('all');
  const [paymentModeFilter, setPaymentModeFilter] = useState('all');
  const [violationFilter, setViolationFilter] = useState('all');
  const [receiptStatusFilter, setReceiptStatusFilter] = useState('all');
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  const [isConnected, setIsConnected] = useState(false);
  const [rawVasData, setRawVasData] = useState<any[]>([]);
  const [rawPosData, setRawPosData] = useState<any[]>([]);
  const [minAmount, setMinAmount] = useState('');
  const [maxAmount, setMaxAmount] = useState('');
  const [selectedTransaction, setSelectedTransaction] = useState<Transaction | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [page, setPage] = useState(0);

  const loadFromLocal = async () => {
    try {
      const [txnRes, alertsRes, storesRes] = await Promise.all([
        fetch(`${BACKEND_BASE}/api/transactions`),
        fetch(`${BACKEND_BASE}/api/alerts`),
        fetch(`${BACKEND_BASE}/api/stores`).catch(() => null),
      ]);

      const txnPayload = await txnRes.json();
      const alertsPayload = await alertsRes.json();
      const storesPayload = storesRes?.ok ? await storesRes.json() : [];

      const names: Record<string, string> = {};
      storesPayload.forEach((store: any) => {
        names[store.cin] = store.name;
      });
      setStoreNames(names);

      const rawTransactions = Array.isArray(txnPayload) ? txnPayload : txnPayload.transactions || [];
      const txns = rawTransactions.map((txn: any) => ({
        ...txn,
        timestamp: new Date(txn.timestamp),
        shop_name: txn.shop_name || names[txn.shop_id] || txn.shop_id,
      }));
      const loadedAlerts = (Array.isArray(alertsPayload) ? alertsPayload : []).map((alert: any) => ({
        ...alert,
        timestamp: new Date(alert.timestamp),
        shop_name: alert.shop_name || names[alert.shop_id] || alert.shop_id,
      }));

      setTransactions(txns);
      setAlerts(loadedAlerts);
      setBillsMap(Array.isArray(txnPayload) ? {} : (txnPayload.bills_map || {}));
    } catch (error) {
      console.error('Failed to load RLCC data:', error);
    }
  };

  const reloadHistoricalData = async () => {
    await loadFromLocal();
    try {
      await fetch(`${BACKEND_BASE}/api/history?days=5`);
    } catch (error) {
      console.error('Failed to refresh sales history:', error);
    }
    await loadFromLocal();
  };

  const reloadAfterConfigChange = async () => {
    await loadFromLocal();
    toast.success('Rules saved');
  };

  const handleOpenTransactionFromAlert = async (alert: Alert) => {
    if (alert.transaction_id && alert.transaction_id !== 'N/A') {
      const existing = transactions.find(txn => txn.id === alert.transaction_id);
      if (existing) {
        await handleRowClick(existing);
        return;
      }

      try {
        const response = await fetch(`${BACKEND_BASE}/api/transactions/${alert.transaction_id}`);
        if (response.ok) {
          const payload = await response.json();
          const detailed = {
            ...payload.transaction,
            shop_name: payload.transaction.shop_name || alert.shop_name || payload.transaction.shop_id,
            cam_id: payload.transaction.cam_id || alert.cam_id || '',
            pos_id: payload.transaction.pos_id || alert.pos_id || '',
            cashier_name: payload.transaction.cashier_name || alert.cashier_name,
            clip_url: payload.transaction.clip_url || alert.clip_url,
            clip_reason: payload.transaction.clip_url ? undefined : alert.clip_reason,
            timestamp: new Date(payload.transaction.timestamp),
          };
          setSelectedTransaction(detailed);
          setBillsMap(prev => ({ ...prev, [payload.transaction.id]: payload.bill_data || prev[payload.transaction.id] }));
          setDrawerOpen(true);
          return;
        }
      } catch {
        // Fall back to alert-backed clip below when the transaction record is not available.
      }
    }
    const synthetic: Transaction = {
      id: alert.id,
      shop_id: alert.shop_id,
      shop_name: alert.shop_name,
      cam_id: alert.cam_id || '',
      pos_id: alert.pos_id || '',
      cashier_name: alert.cashier_name,
      timestamp: alert.timestamp,
      transaction_total: 0,
      risk_level: alert.risk_level,
      triggered_rules: alert.triggered_rules,
      status: alert.status,
      clip_url: alert.clip_url,
      clip_reason: alert.clip_reason,
    };
    setSelectedTransaction(synthetic);
    setDrawerOpen(true);
  };

  const handleRowClick = async (transaction: Transaction) => {
    try {
      const response = await fetch(`${BACKEND_BASE}/api/transactions/${transaction.id}`);
      if (!response.ok) {
        setSelectedTransaction(transaction);
      } else {
        const payload = await response.json();
        const detailed = {
          ...transaction,
          ...payload.transaction,
          timestamp: new Date(payload.transaction.timestamp),
        };
        setSelectedTransaction(detailed);
        setBillsMap(prev => ({ ...prev, [transaction.id]: payload.bill_data || prev[transaction.id] }));
      }
    } catch {
      setSelectedTransaction(transaction);
    }
    setDrawerOpen(true);
  };

  useEffect(() => {
    reloadHistoricalData().catch(() => {});
  }, []);

  useEffect(() => {
    const ws = new WebSocket(`${BACKEND_WS_BASE}/ws`);
    ws.onopen = () => {
      setIsConnected(true);
      toast.success('Connected to RLCC backend');
    };
    ws.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'NEW_TRANSACTION') {
          const txn = {
            ...message.data,
            timestamp: new Date(message.data.timestamp),
            shop_name: message.data.shop_name || message.data.shop_id,
          };
          setTransactions(prev => [txn, ...prev]);
        } else if (message.type === 'NEW_ALERT') {
          const alert = {
            ...message.data,
            timestamp: new Date(message.data.timestamp),
            shop_name: message.data.shop_name || message.data.shop_id,
          };
          setAlerts(prev => [alert, ...prev]);
        } else if (message.type === 'TRANSACTION_UPDATE') {
          const { id, status, notes } = message.data;
          setTransactions(prev => prev.map(txn => (txn.id === id ? { ...txn, status, notes } : txn)));
          setAlerts(prev => prev.map(alert => (alert.transaction_id === id ? { ...alert, status, remarks: notes } : alert)));
        } else if (message.type === 'ALERT_UPDATED') {
          const { id, status, remarks } = message.data;
          setAlerts(prev => prev.map(alert => (alert.id === id ? { ...alert, status, remarks } : alert)));
        } else if (message.type === 'RAW_VAS_DATA') {
          setRawVasData(message.data);
        } else if (message.type === 'RAW_POS_DATA') {
          setRawPosData(message.data);
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };
    ws.onclose = () => {
      setIsConnected(false);
    };
    return () => ws.close();
  }, []);

  const timeFilteredTransactions = useMemo(() => {
    if (timeRange === 'all') return transactions;
    const now = new Date();
    let cutoff: Date;
    switch (timeRange) {
      case 'today':
        cutoff = startOfDay(now);
        break;
      case '2days':
        cutoff = startOfDay(subDays(now, 2));
        break;
      case 'week':
        cutoff = startOfDay(subDays(now, 7));
        break;
      default:
        return transactions;
    }
    return transactions.filter(txn => txn.timestamp >= cutoff);
  }, [transactions, timeRange]);

  const uniqueStores = useMemo(() => {
    const map = new Map<string, string>();
    transactions.forEach(txn => {
      if (!map.has(txn.shop_id)) {
        map.set(txn.shop_id, txn.shop_name || storeNames[txn.shop_id] || txn.shop_id);
      }
    });
    return Array.from(map.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [transactions, storeNames]);

  const paymentModes = useMemo(() => {
    const values = new Set<string>();
    transactions.forEach(txn => {
      txn.payments?.forEach(payment => {
        values.add(payment.line_attribute || payment.payment_description || 'Unknown');
      });
    });
    return Array.from(values).sort();
  }, [transactions]);

  const filteredTransactions = useMemo(() => {
    const min = parseFloat(minAmount);
    const max = parseFloat(maxAmount);
    const search = searchTerm.toLowerCase();

    return timeFilteredTransactions.filter(txn => {
      if (search) {
        const haystack = [txn.id, txn.cashier_name, txn.shop_id, txn.shop_name || ''].join(' ').toLowerCase();
        if (!haystack.includes(search)) return false;
      }
      if (storeFilter !== 'all' && txn.shop_id !== storeFilter) return false;
      if (activeFilter === 'high' && txn.risk_level !== 'High') return false;
      if (activeFilter === 'medium' && txn.risk_level !== 'Medium') return false;
      if (activeFilter === 'pending' && txn.status && !['pending', 'new', 'reviewing'].includes(txn.status)) return false;
      if (!Number.isNaN(min) && txn.transaction_total < min) return false;
      if (!Number.isNaN(max) && txn.transaction_total > max) return false;
      if (paymentModeFilter !== 'all' && !(txn.payments || []).some(payment => (payment.line_attribute || payment.payment_description || 'Unknown') === paymentModeFilter)) return false;
      if (!matchesViolation(txn, violationFilter)) return false;
      if (receiptStatusFilter !== 'all' && (txn.receipt_status || 'unknown') !== receiptStatusFilter) return false;
      return true;
    });
  }, [timeFilteredTransactions, searchTerm, storeFilter, activeFilter, minAmount, maxAmount, paymentModeFilter, violationFilter, receiptStatusFilter]);

  const PAGE_SIZE = 50;
  const totalPages = Math.ceil(filteredTransactions.length / PAGE_SIZE);
  const paginatedTransactions = useMemo(
    () => filteredTransactions.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [filteredTransactions, page],
  );

  useEffect(() => {
    setPage(0);
  }, [searchTerm, storeFilter, activeFilter, timeRange, minAmount, maxAmount, paymentModeFilter, violationFilter, receiptStatusFilter]);

  const highCount = timeFilteredTransactions.filter(txn => txn.risk_level === 'High').length;
  const mediumCount = timeFilteredTransactions.filter(txn => txn.risk_level === 'Medium').length;
  const openAlertCount = alerts.filter(alert => ['new', 'Fraudulent', 'Pending for review', 'reviewing'].includes(alert.status)).length;

  const clearFilters = () => {
    setActiveFilter('all');
    setSearchTerm('');
    setTimeRange('all');
    setStoreFilter('all');
    setPaymentModeFilter('all');
    setViolationFilter('all');
    setReceiptStatusFilter('all');
    setMinAmount('');
    setMaxAmount('');
  };

  return (
    <div className="h-screen flex bg-gray-50">
      <div className="w-56 bg-white border-r border-gray-200 flex flex-col shadow-sm">
        <div className="px-4 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <div className="p-1.5 bg-blue-600 rounded-lg">
              <Shield className="h-4 w-4 text-white" />
            </div>
            <div>
              <h1 className="text-sm font-bold text-gray-800">RLCC</h1>
              <p className="text-[10px] text-gray-400">Revenue Leakage Control Center</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 py-2 px-2 space-y-0.5 overflow-y-auto">
          {NAV_ITEMS.map(item => {
            const isActive = activeTab === item.id;
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all ${
                  isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800'
                }`}
              >
                <Icon className={`h-4 w-4 ${isActive ? 'text-blue-600' : 'text-gray-400'}`} />
                {item.label}
                {item.id === 'alerts' && openAlertCount > 0 && (
                  <Badge className="ml-auto bg-red-100 text-red-700 border-red-200 text-[10px] px-1.5 py-0">
                    {openAlertCount}
                  </Badge>
                )}
              </button>
            );
          })}
        </nav>

        <div className="px-3 py-3 border-t border-gray-100">
          <div className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs ${isConnected ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            <div className={`h-1.5 w-1.5 rounded-full animate-pulse ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            {isConnected ? 'System Active' : 'Disconnected'}
          </div>
        </div>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="bg-gradient-to-r from-blue-700 via-blue-600 to-blue-800 px-5 py-2.5 shadow-sm">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-3 flex-1">
              <Card className={`bg-white/10 backdrop-blur-sm border-white/20 px-3 py-1.5 cursor-pointer transition-all hover:bg-white/20 ${activeFilter === 'all' ? 'ring-1 ring-white/50' : ''}`} onClick={() => setActiveFilter('all')}>
                <div className="flex items-center gap-2">
                  <LayoutDashboard className="h-3.5 w-3.5 text-blue-200" />
                  <span className="text-xs text-blue-100">Transactions</span>
                  <span className="text-sm font-bold text-white"><AnimatedCount value={timeFilteredTransactions.length} /></span>
                </div>
              </Card>
              <Card className={`bg-red-500/20 backdrop-blur-sm border-red-300/30 px-3 py-1.5 cursor-pointer transition-all hover:bg-red-500/30 ${activeFilter === 'high' ? 'ring-1 ring-red-300' : ''}`} onClick={() => setActiveFilter('high')}>
                <div className="flex items-center gap-2">
                  <ShieldAlert className="h-3.5 w-3.5 text-red-200" />
                  <span className="text-xs text-red-200">High Risk</span>
                  <span className="text-sm font-bold text-white"><AnimatedCount value={highCount} /></span>
                </div>
              </Card>
              <Card className={`bg-amber-500/20 backdrop-blur-sm border-amber-300/30 px-3 py-1.5 cursor-pointer transition-all hover:bg-amber-500/30 ${activeFilter === 'medium' ? 'ring-1 ring-amber-300' : ''}`} onClick={() => setActiveFilter('medium')}>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-200" />
                  <span className="text-xs text-amber-200">Medium Risk</span>
                  <span className="text-sm font-bold text-white"><AnimatedCount value={mediumCount} /></span>
                </div>
              </Card>
              <Card className="bg-white/10 backdrop-blur-sm border-white/20 px-3 py-1.5 cursor-pointer transition-all hover:bg-white/20" onClick={() => setActiveTab('alerts')}>
                <div className="flex items-center gap-2">
                  <Bell className="h-3.5 w-3.5 text-blue-200" />
                  <span className="text-xs text-blue-100">Alerts</span>
                  <span className="text-sm font-bold text-white"><AnimatedCount value={openAlertCount} /></span>
                </div>
              </Card>
            </div>
            <Button variant="outline" size="sm" className="gap-1.5 h-7 text-xs border-white/30 text-white hover:bg-white/10 bg-transparent" onClick={() => { reloadHistoricalData().catch(() => {}); toast.success('Refreshing...'); }}>
              <RefreshCw className="h-3 w-3" /> Refresh
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-5">
          {activeTab === 'transactions' && (
            <div className="space-y-4">
              <Card className="bg-white border-gray-200 p-3 shadow-sm">
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="relative flex-1 min-w-[220px]">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                    <Input placeholder="Search by ID, cashier, or store..." value={searchTerm} onChange={event => setSearchTerm(event.target.value)} className="pl-10 bg-gray-50 border-gray-200 h-9" />
                  </div>
                  <Select value={timeRange} onValueChange={setTimeRange}>
                    <SelectTrigger className="w-[120px] bg-white border-gray-200 h-9 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Time</SelectItem>
                      <SelectItem value="today">Today</SelectItem>
                      <SelectItem value="2days">Last 2 Days</SelectItem>
                      <SelectItem value="week">Last Week</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select value={storeFilter} onValueChange={setStoreFilter}>
                    <SelectTrigger className="w-[160px] bg-white border-gray-200 h-9 text-xs"><SelectValue placeholder="All Stores" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Stores</SelectItem>
                      {uniqueStores.map(([id, name]) => (
                        <SelectItem key={id} value={id}>{name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select value={paymentModeFilter} onValueChange={setPaymentModeFilter}>
                    <SelectTrigger className="w-[160px] bg-white border-gray-200 h-9 text-xs"><SelectValue placeholder="Payment Mode" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Payments</SelectItem>
                      {paymentModes.map(mode => (
                        <SelectItem key={mode} value={mode}>{mode}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select value={violationFilter} onValueChange={setViolationFilter}>
                    <SelectTrigger className="w-[170px] bg-white border-gray-200 h-9 text-xs"><SelectValue placeholder="Violation Type" /></SelectTrigger>
                    <SelectContent>
                      {RULE_FILTERS.map(option => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select value={receiptStatusFilter} onValueChange={setReceiptStatusFilter}>
                    <SelectTrigger className="w-[150px] bg-white border-gray-200 h-9 text-xs"><SelectValue placeholder="Receipt Status" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Any Receipt</SelectItem>
                      <SelectItem value="generated">Generated</SelectItem>
                      <SelectItem value="not_generated">Not Generated</SelectItem>
                      <SelectItem value="unknown">Unknown</SelectItem>
                    </SelectContent>
                  </Select>
                  <div className="flex items-center gap-1">
                    <Input type="number" placeholder="Min ₹" value={minAmount} onChange={event => setMinAmount(event.target.value)} className="w-[90px] bg-gray-50 border-gray-200 text-xs h-9" />
                    <span className="text-gray-300">-</span>
                    <Input type="number" placeholder="Max ₹" value={maxAmount} onChange={event => setMaxAmount(event.target.value)} className="w-[90px] bg-gray-50 border-gray-200 text-xs h-9" />
                  </div>
                  <Button variant="outline" size="sm" className="gap-1 border-gray-200 h-9 text-xs" onClick={clearFilters}>
                    <Filter className="h-3 w-3" /> Clear
                  </Button>
                  <Button variant="outline" size="sm" className="gap-1 border-gray-200 text-blue-600 hover:bg-blue-50 h-9 text-xs" onClick={() => { exportToCSV(filteredTransactions); toast.success(`Exported ${filteredTransactions.length} transactions`); }}>
                    <Download className="h-3 w-3" /> CSV
                  </Button>
                </div>
              </Card>

              <TransactionTable transactions={paginatedTransactions} onRowClick={handleRowClick} />

              <div className="flex items-center justify-between text-sm text-gray-500">
                <span>Showing {filteredTransactions.length > 0 ? page * PAGE_SIZE + 1 : 0}-{Math.min((page + 1) * PAGE_SIZE, filteredTransactions.length)} of {filteredTransactions.length}</span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(prev => prev - 1)} className="border-gray-200 h-7 text-xs">Previous</Button>
                  <span className="text-gray-600 text-xs font-medium">Page {page + 1} of {totalPages || 1}</span>
                  <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(prev => prev + 1)} className="border-gray-200 h-7 text-xs">Next</Button>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'analytics' && <AnalyticsView transactions={timeFilteredTransactions} />}
          {activeTab === 'alerts' && <AlertWorkflow alerts={alerts} setAlerts={setAlerts} transactions={transactions} onOpenTransaction={handleOpenTransactionFromAlert} />}
          {activeTab === 'videos' && <VideoReviewView alerts={alerts} transactions={transactions} onOpenAlert={handleOpenTransactionFromAlert} onOpenTransaction={handleRowClick} />}
          {activeTab === 'scorecard' && <EmployeeScorecardView transactions={timeFilteredTransactions} storeNames={storeNames} />}
          {activeTab === 'heatmap' && <HeatmapView transactions={timeFilteredTransactions} storeNames={storeNames} />}
          {activeTab === 'streams' && <StreamViewer vasData={rawVasData} posData={rawPosData} />}
          {activeTab === 'store-config' && <StoreConfigView />}
          {activeTab === 'settings' && <SettingsPanel onConfigSaved={reloadAfterConfigChange} />}
        </div>
      </div>

      <TransactionDetailDrawer
        transaction={selectedTransaction}
        billData={selectedTransaction ? billsMap[selectedTransaction.id] : null}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
