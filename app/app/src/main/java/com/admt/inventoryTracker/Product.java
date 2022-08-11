package com.admt.inventoryTracker;

public class Product {
    private Integer id;
    public String Barcode;
    public Boolean CanExpire;
    public Boolean IsBulkProduct;
    public String Name;
    public Boolean IsAssignedStockId;
    public String AssociatedStockId;

    public Product(String Name, String Barcode, Boolean CanExpire, Boolean IsBulk, Boolean IsAssignedStockId, String AssociatedStockId){
        this.Name = Name;
        this.Barcode = Barcode;
        this.CanExpire = CanExpire;
        this.IsBulkProduct = IsBulk;
        this.IsAssignedStockId = IsAssignedStockId;
        this.AssociatedStockId = AssociatedStockId;
    }

}