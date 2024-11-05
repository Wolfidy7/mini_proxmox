from flask import Flask, render_template, request, redirect, url_for, flash
import libvirt

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Nécessaire pour utiliser les messages flash

# Connexion à l'hyperviseur
def get_connection():
    return libvirt.open('qemu:///system')

# Affiche la liste des VMs
@app.route('/vms', methods=['GET'])
def list_vms():
    conn = get_connection()
    vms = {}
    
    for id in conn.listDomainsID():
        domain = conn.lookupByID(id)
        vms[domain.name()] = {
            'state': domain.state()[0],
            'uuid': domain.UUIDString()
        }
    
    conn.close()
    return render_template('vms.html', vms=vms)

# Crée une nouvelle VM
@app.route('/create_vm', methods=['GET', 'POST'])
def create_vm():
    if request.method == 'POST':
        vm_name = request.form['vm_name']
        ram_size = int(request.form['ram_size'])  # en MiB
        vcpu_count = int(request.form['vcpu_count'])
        
        # XML de la configuration de la VM
        vm_xml = f"""
        <domain type='kvm'>
          <name>{vm_name}</name>
          <memory unit='MiB'>{ram_size}</memory>
          <vcpu placement='static'>{vcpu_count}</vcpu>
          <os>
            <type arch='x86_64' machine='pc-i440fx-2.9'>hvm</type>
            <boot dev='hd'/>
          </os>
          <disk type='file' device='disk'>
            <driver name='qemu' type='qcow2'/>
            <source file='/var/lib/libvirt/images/{vm_name}.qcow2'/>
            <target dev='vda' bus='virtio'/>
            <address type='pci' domain='0x0000' bus='0x00' slot='0x04' function='0x0'/>
          </disk>
          <interface type='network'>
            <mac address='52:54:00:xx:xx:xx'/>
            <source network='default'/>
            <model type='virtio'/>
          </interface>
        </domain>
        """

        # Connexion à l'hyperviseur et création de la VM
        conn = get_connection()
        try:
            conn.createXML(vm_xml, 0)
            flash(f'La VM "{vm_name}" a été créée avec succès !', 'success')
        except libvirt.libvirtError as e:
            flash(f'Erreur lors de la création de la VM : {e}', 'error')
        finally:
            conn.close()
        return redirect(url_for('list_vms'))
    
    return render_template('create_vm.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
